"""Regression tests against the actual blueprint templates and action sequences.

The small runner models HA actions and charger responses, not HA's event loop.
No network access or real charger commands are used.
"""
import ast
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest

import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment


class Loader(yaml.SafeLoader):
    pass


Loader.add_constructor('!input', lambda loader, node: {'input_ref': loader.construct_scalar(node)})
BLUEPRINT = Path(__file__).resolve().parents[1] / 'smart_ev_load_limiter.yaml'
if not BLUEPRINT.exists():
    BLUEPRINT = Path(__file__).with_name('smart_ev_load_limiter.yaml')
CONFIG = yaml.load(BLUEPRINT.read_text(encoding='utf-8'), Loader=Loader)


class States:
    def __init__(self, now):
        self.now = now
        self.data = {}

    def set(self, entity, state, **attrs):
        old = self.data.get(entity)
        self.data[entity] = SimpleNamespace(
            state=str(state), attributes=attrs or (old.attributes if old else {}),
            last_changed=old.last_changed if old and old.state == str(state) else self.now(),
            context=SimpleNamespace(user_id=None))

    def __call__(self, entity):
        return self.data[entity].state if entity in self.data else 'unknown'

    def __getitem__(self, entity):
        return self.data.get(entity)


class Runner:
    def __init__(self):
        self.time = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
        self.states = States(lambda: self.time)
        self.env = NativeEnvironment(undefined=StrictUndefined)
        self.env.filters['bool'] = bool
        self.env.globals.update(states=self.states, is_state=lambda e, s: self.states(e) == s,
            state_attr=lambda e, k: self.states[e].attributes.get(k) if self.states[e] else None,
            now=lambda: self.time, as_timestamp=self.timestamp,
            as_datetime=lambda s, default=None: self.date(s, default))
        self.inputs = {k: v.get('default') for k, v in CONFIG['blueprint']['input'].items()}
        self.inputs.update(house_power_sensor='sensor.house', ev_power_sensor='sensor.ev',
            charger_limit_entity='number.limit', charge_state_sensor='sensor.charge',
            stop_charging_button='button.stop', start_charging_button='button.start',
            auto_stop_tracking_helper='input_boolean.stopped', stop_when_below_minimum=True,
            house_max_power_w=6000, minimum_adjustment_amps=1)
        for entity, state, attrs in [
            ('sensor.house', 2000, {'unit_of_measurement': 'W'}),
            ('sensor.ev', 0, {'unit_of_measurement': 'W'}),
            ('number.limit', 6, {'min': 6, 'max': 20}),
            ('sensor.charge', 'ev_connected', {}),
            ('button.stop', 'unknown', {}), ('button.start', 'unknown', {}),
            ('input_boolean.stopped', 'on', {})]:
            self.states.set(entity, state, **attrs)
        self.time += timedelta(hours=1)
        self.calls = []
        self.response = {'button.start': 'charging', 'button.stop': 'ev_connected'}
        self.fail = set()
        self.wait_hook = None
        self.trigger = {'id': 'periodic_check'}

    @staticmethod
    def date(value, default=None):
        try:
            return datetime.fromisoformat(value) if isinstance(value, str) else value
        except (ValueError, TypeError):
            return default

    @classmethod
    def timestamp(cls, value, default=0):
        try:
            return cls.date(value).timestamp()
        except (AttributeError, TypeError, ValueError):
            return default

    def render(self, value, context):
        if not isinstance(value, str) or ('{{' not in value and '{%' not in value):
            return value
        result = self.env.from_string(value).render(**context)
        if isinstance(result, str):
            result = result.strip()
            if result.lower() in ('true', 'false', 'none'):
                return {'true': True, 'false': False, 'none': None}[result.lower()]
            try:
                return ast.literal_eval(result)
            except (ValueError, SyntaxError):
                return result
        return result

    def resolve(self, value):
        if isinstance(value, dict):
            if 'input_ref' in value:
                return self.inputs[value['input_ref']]
            return {k: self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        return value

    def context(self):
        ctx = self.resolve(CONFIG['trigger_variables'])
        ctx.update(self.resolve(CONFIG['variables']))
        ctx['trigger'] = self.trigger
        for k, v in CONFIG['action'][0]['variables'].items():
            ctx[k] = self.render(v, ctx)
        return ctx

    def condition(self, c, ctx):
        if c['condition'] == 'trigger':
            return self.trigger['id'] == c['id']
        assert c['condition'] == 'template'
        return bool(self.render(c['value_template'], ctx))

    def sequence(self, actions, ctx):
        for a in actions:
            if 'if' in a:
                if all(self.condition(c, ctx) for c in a['if']):
                    self.sequence(a['then'], ctx)
            elif 'wait_template' in a:
                if not self.render(a['wait_template'], ctx):
                    if self.wait_hook:
                        self.wait_hook(self)
                    if not self.render(a['wait_template'], ctx):
                        h, m, s = map(int, a['timeout'].split(':'))
                        self.time += timedelta(hours=h, minutes=m, seconds=s)
            elif 'service' in a:
                service = a['service']
                entity = self.render(self.resolve(a['target']['entity_id']), ctx)
                self.calls.append((service, entity))
                if service in self.fail:
                    if a.get('continue_on_error'):
                        continue
                    raise RuntimeError(service)
                if service.startswith('input_boolean.'):
                    self.states.set(entity, 'on' if service.endswith('turn_on') else 'off')
                elif service == 'number.set_value':
                    self.states.set(entity, self.render(a['data']['value'], ctx))
                elif service == 'button.press':
                    self.states.set(entity, self.time.isoformat())
                    if self.response.get(entity):
                        self.states.set('sensor.charge', self.response[entity])
                else:
                    raise AssertionError(service)
            else:
                raise AssertionError(a)

    def run(self):
        ctx = self.context()
        for branch in CONFIG['action'][1]['choose']:
            if all(self.condition(c, ctx) for c in branch['conditions']):
                self.sequence(branch['sequence'], ctx)
                break
        return ctx


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.r = Runner()

    def test_no_headroom_does_not_restart_despite_clamp(self):
        for load in (6000, 5000, 2041):
            with self.subTest(load=load):
                self.r.states.set('sensor.house', load)
                c = self.r.run()
                self.assertEqual(c['computed_target_amps'], 6)
                self.assertFalse(c['should_start_after_auto_stop'])
                self.assertEqual(self.r.calls, [])

    def test_exact_minimum_headroom_starts_at_safe_limit(self):
        self.r.states.set('sensor.house', 2040)  # (6000 - 2040) / 660 = 6 A
        self.r.states.set('number.limit', 20)
        self.r.run()
        self.assertEqual(self.r.states('number.limit'), '6')
        self.assertEqual(self.r.calls[:2], [('number.set_value', 'number.limit'), ('button.press', 'button.start')])
        self.assertEqual(self.r.states('input_boolean.stopped'), 'off')

    def test_unavailable_start_keeps_ownership(self):
        self.r.states.set('button.start', 'unavailable')
        self.r.run()
        self.assertEqual(self.r.calls, [])
        self.assertEqual(self.r.states('input_boolean.stopped'), 'on')

    def test_manual_stop_is_not_restarted(self):
        self.r.states.set('input_boolean.stopped', 'off')
        self.r.run()
        self.assertNotIn(('button.press', 'button.start'), self.r.calls)

    def test_unconfirmed_start_preserves_flag_and_respects_retry_cooldown(self):
        self.r.response['button.start'] = None
        self.r.run()
        self.assertEqual(self.r.states('input_boolean.stopped'), 'on')
        self.r.run()
        self.assertEqual(self.r.calls.count(('button.press', 'button.start')), 1)
        self.r.time += timedelta(minutes=15)
        self.r.run()
        self.assertEqual(self.r.calls.count(('button.press', 'button.start')), 2)

    def test_failed_start_retains_flag_and_backs_off(self):
        self.r.fail.add('button.press')
        before = self.r.time
        self.r.run()
        self.assertEqual(self.r.time - before, timedelta(minutes=1))
        self.assertEqual(self.r.states('input_boolean.stopped'), 'on')

    def test_late_confirmation_on_periodic_run_clears_tracking(self):
        self.r.states.set('sensor.charge', 'charging')
        self.r.run()
        self.assertEqual(self.r.calls, [('input_boolean.turn_off', 'input_boolean.stopped')])

    def test_confirmation_during_wait(self):
        self.r.response['button.start'] = None
        self.r.wait_hook = lambda r: r.states.set('sensor.charge', 'charging')
        self.r.run()
        self.assertEqual(self.r.states('input_boolean.stopped'), 'off')

    def test_unknown_charger_does_not_clear_ownership(self):
        for state in ('unknown', 'unavailable', 'unrecognized'):
            self.r.states.set('sensor.charge', state)
            self.r.run()
            self.assertEqual(self.r.states('input_boolean.stopped'), 'on')
            self.assertEqual(self.r.calls, [])

    def test_disconnected_charger_clears_ownership(self):
        self.r.states.set('sensor.charge', 'idle')
        self.r.run()
        self.assertEqual(self.r.states('input_boolean.stopped'), 'off')

    def test_missing_house_reading_blocks_restart(self):
        self.r.states.set('sensor.house', 'unavailable')
        self.r.run()
        self.assertEqual(self.r.calls, [])

    def test_confirmation_reconciles_even_without_house_reading(self):
        self.r.states.set('sensor.house', 'unavailable')
        self.r.states.set('sensor.charge', 'charging')
        self.r.run()
        self.assertEqual(self.r.states('input_boolean.stopped'), 'off')

    def setup_stop(self):
        self.r.states.set('input_boolean.stopped', 'off')
        self.r.time += timedelta(minutes=20)
        self.r.states.set('sensor.charge', 'charging')
        self.r.states.set('sensor.house', 8000)
        self.r.states.set('sensor.ev', 3960)
        self.r.trigger = {'id': 'below_minimum_persisted'}

    def test_stop_records_ownership_before_charger_command(self):
        self.setup_stop()
        self.r.run()
        self.assertEqual(self.r.calls, [('input_boolean.turn_on', 'input_boolean.stopped'), ('button.press', 'button.stop')])
        self.assertEqual(self.r.states('input_boolean.stopped'), 'on')
        self.r.trigger = {'id': 'periodic_check'}
        self.r.run()
        self.assertEqual(len(self.r.calls), 2)

    def test_failed_stop_reconciles_and_keeps_normal_limiting(self):
        self.setup_stop()
        self.r.fail.add('button.press')
        self.r.run()
        self.assertEqual(self.r.states('input_boolean.stopped'), 'on')
        self.r.trigger = {'id': 'periodic_check'}
        self.r.run()
        self.assertEqual(self.r.states('input_boolean.stopped'), 'off')

    def test_low_headroom_timer_is_rearmed_after_cooldown(self):
        self.setup_stop()
        self.r.states.set('input_boolean.stopped', 'on')
        self.r.states.set('input_boolean.stopped', 'off')
        trigger = next(t for t in CONFIG['trigger'] if t['id'] == 'below_minimum_persisted')
        ctx = self.r.resolve(CONFIG['trigger_variables'])
        self.assertFalse(self.r.render(trigger['value_template'], ctx))
        self.r.time += timedelta(minutes=15)
        self.assertTrue(self.r.render(trigger['value_template'], ctx))
        self.assertEqual(self.r.resolve(trigger['for']), {'minutes': 5})

    def test_queued_mode_prevents_state_update_cancelling_commands(self):
        # HA implements queuing. Assert the contract our sequence relies on.
        self.assertEqual(CONFIG['mode'], 'queued')
        self.assertGreater(CONFIG['max'], 1)
        self.setup_stop()
        self.r.run()  # Stop changes the charge state during the command.
        self.r.trigger = {'id': 'charge_state_changed', 'to_state': self.r.states['sensor.charge'], 'from_state': None}
        self.r.run()  # Its queued run uses fresh state and retains ownership.
        self.assertEqual(self.r.states('input_boolean.stopped'), 'on')

    def test_failed_current_write_does_not_press_start(self):
        self.r.states.set('number.limit', 20)
        self.r.fail.add('number.set_value')
        with self.assertRaises(RuntimeError):
            self.r.run()
        self.assertNotIn(('button.press', 'button.start'), self.r.calls)
        self.assertEqual(self.r.states('input_boolean.stopped'), 'on')


if __name__ == '__main__':
    unittest.main()
