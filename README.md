# Smart EV Load Limiter

Smart EV Load Limiter is a Home Assistant automation blueprint that continuously adjusts your EV charger's current limit to keep total household power below a configured cap. It is designed for setups such as Tibber plus DEFA, but it stays integration-agnostic by reading generic Home Assistant entities.

## Features
- Caps charging current based on available household power headroom
- Supports either whole-house power including EV load or a base-load sensor that already excludes EV charging
- Converts watts to charger amps using configurable phase count and voltage
- Respects a persistent user-defined preferred max amps value instead of learning from a possibly temporary throttled charger state
- Supports always-on mode or a weekday/time schedule, including overnight windows
- Allows timed snooze via an `input_button` helper so you can temporarily override the limiter manually
- Treats manual charger amp changes from the Home Assistant UI as a temporary override for the snooze duration
- Limits the entity pickers to power sensors and current-setting number entities so the setup UI is easier to understand
- Can optionally notify you when it actively reduces the charger below your preferred max current
- Dampens control-loop hunting with a minimum amp-change threshold and cooldown between automatic charger updates

## Requirements
- Home Assistant with:
  - a live household power sensor in `W` or `kW`
  - a charger max-current `number` entity that accepts integer amps
  - a charge-state entity that indicates when charging is active
- Optional:
  - a live EV charging power sensor in `W` or `kW` for more accurate limiting in `whole_house_including_ev` mode
  - an `input_button` helper for the snooze action

## Installation
1. Download `smart_ev_load_limiter.yaml`.
2. In Home Assistant, go to **Settings > Automations & Scenes > Blueprints** and import the blueprint.
3. Create a new automation from **Smart EV Load Limiter**.

Manual install: copy `smart_ev_load_limiter.yaml` into `config/blueprints/automation/smart_ev_load_limiter/` and reload automations.

If you update the blueprint after creating an automation from it, open the automation and save it again so Home Assistant refreshes the generated automation config. When inputs or defaults have changed substantially, recreating the automation from the updated blueprint may be the safest path.

## Configuration Inputs
| Input | Description | Default |
| --- | --- | --- |
| **House power sensor** | Live household power sensor in `W` or `kW`. The picker is filtered to power sensors. | - |
| **Power accounting mode** | Whether the house power already excludes EV load or whether the blueprint should subtract EV charging power from the total. | `whole_house_including_ev` |
| **EV charging power sensor** | Optional actual EV charging power sensor. In `whole_house_including_ev` mode the blueprint prefers this live reading and otherwise estimates EV power from the current charger amp limit. The picker is filtered to power sensors, and this field can now be left empty. | Empty |
| **Charger current limit entity** | `number` entity used to set charger amps. The picker is filtered to number entities with device class `current`, which should match the DEFA max-current control. | - |
| **Charge state sensor** | Sensor or binary sensor that reports charging state. | - |
| **Charging states** | Sensor states treated as an active charging session. | `["charging"]` |
| **Paused session states** | Sensor states that mean the session is paused but still connected. While in one of these states, the blueprint leaves the charger current untouched. | `["suspended_evse", "suspended_ev"]` |
| **House max power** | Hard household power cap in watts. | `10500` |
| **Preferred max charging current** | User-selected max charger current while actively charging. The blueprint never exceeds this while limiting. | `16 A` |
| **Non-charging current limit** | Current limit restored when the limiter is inactive or the car is not charging. Use this as the safer fallback current when the blueprint is idle. | `16 A` |
| **Minimum charging current** | Lowest usable charging current. | `6 A` |
| **Minimum limiter current** | Lowest current the blueprint is allowed to set while it is actively limiting. Set above the charger minimum if you want to avoid very slow charging. | `0 A` |
| **Charging phase count** | Number of phases used for the watts-to-amps conversion. | `3` |
| **Voltage per phase** | Voltage used for the watts-to-amps conversion. | `220 V` |
| **Always active** | Run every day, all day. | `true` |
| **Active weekdays** | Used only when always-active is off. | All days |
| **Active start/end time** | Active schedule window, including overnight windows. | `00:00:00` / `00:00:00` |
| **Poll interval** | Safety recheck interval. | `1 min` |
| **Minimum adjustment delta** | Only apply a new charger limit when the calculated target differs by at least this many amps. | `2 A` |
| **Adjustment cooldown** | Minimum wait time between automatic charger current changes. | `45 s` |
| **Enable notifications** | Notify when the limiter actively reduces the charger below your preferred max current. | `false` |
| **Devices to notify** | Mobile App devices that should receive limiter notifications. | `[]` |
| **Create Home Assistant notification** | Also create a persistent notification in Home Assistant when limiter notifications are enabled. | `true` |
| **Snooze button** | Optional `input_button` helper to suspend limiter writes temporarily. Leave empty to disable snooze-button control. | Empty |
| **Snooze duration** | How long snooze or a manual charger-limit change bypasses the limiter. | `2 hours` |

## How It Works
1. If the blueprint is snoozed, it does nothing and leaves the charger untouched.
2. If the car is in one of the configured paused session states, it does nothing and leaves the current limit untouched.
3. If the limiter is outside its active schedule, or the car is not charging, it restores the charger to **Non-charging current limit**.
4. If charging is active:
   - it reads the house power
   - in `whole_house_including_ev` mode it subtracts EV charging power to estimate non-EV base load
   - if an EV power sensor is available, it uses the actual live EV charging power
   - otherwise it estimates EV charging power from `current charger amps * phases * volts`
   - it computes remaining power headroom
   - it converts that headroom to amps with `amps = floor(watts / (phases * volts))`
   - it clamps the result to the configured preferred maximum
   - it only applies a new charger limit when the change is large enough and the cooldown has elapsed
5. If the calculated amps fall below the configured minimum:
   - it sets `0 A` when the charger entity allows zero
   - otherwise it falls back to the configured minimum

The blueprint always treats **Preferred max charging current** as the authoritative charging ceiling while a session is active. It does not learn a new baseline from the current DEFA number value, because that value may only be a temporary limiter result during an active charging session or after a Home Assistant restart.

Using a real EV power sensor is more accurate than estimating from the current charger limit. The estimate is still useful, but it assumes the car is drawing the full allowed current, which may not be true during ramp-up, near full battery, or when the vehicle reduces draw on its own.

For DEFA, the charge state may switch from `charging` to `suspended_evse` when the EVSE pauses the session. By default, this blueprint treats that as a paused session and leaves the charger current untouched until charging resumes or the session fully ends.

## Manual Override Behavior
- Pressing the snooze button starts a timed bypass.
- Pressing the snooze button also immediately restores the charger to **Preferred max charging current** so the override actually gives you full normal charging.
- While snoozed, the blueprint stops writing to the charger current limit.
- Manual changes to the charger current limit from the Home Assistant UI are also treated as a temporary override for the snooze duration.
- When snooze expires, the blueprint resumes by recalculating from live sensor values instead of restoring a stale previously-limited amp value.

## Notifications
- When **Enable notifications** is on, the blueprint notifies only when it actively lowers the charger below both the current setting and your preferred max current.
- It does not notify on normal restores back to your configured charging or non-charging limits, upward adjustments while still limited, or while snoozed.
- It can send to selected Mobile App devices.
- It can also create a persistent Home Assistant notification.

## Debugging
- When the automation trace shows no action, open **Changed variables** on the last `choose` step and check: `sensor_values_available`, `snooze_active`, `is_paused_session`, `schedule_active`, `is_charging`, `computed_target_amps`, `target_delta_amps`, `adjustment_threshold_met`, and `adjustment_cooldown_elapsed`.
- If `current_limit_amps` already equals `computed_target_amps`, the blueprint is intentionally doing nothing.
- If `snooze_active` is `true`, the limiter is bypassed either because the snooze button is still active or because a recent manual current change was treated as a temporary override.

## Notes
- If your charger or vehicle only uses one phase, set **Charging phase count** accordingly.
- If you want a safer fallback when the limiter is idle or something goes wrong, set **Non-charging current limit** lower than **Preferred max charging current**.
- If your charger `number` entity cannot be set to `0`, the blueprint cannot strictly enforce the household cap when the safe current would be below the minimum supported charging current.
- The periodic recheck is also what resumes limiter control after a snooze expires if no relevant entity changes occur exactly at that moment.
- Automatic snooze from manual charger changes depends on Home Assistant receiving that charger-limit change with a user context, which is true for normal UI edits but may not be true for every third-party app path.
- The charger current entity is no longer used as a direct trigger, which reduces self-induced feedback loops after the blueprint changes the charger limit itself.
- The snooze button now uses a direct trigger on the configured `input_button`, so unrelated button presses elsewhere in Home Assistant should no longer create extra no-op traces.

## License
This project is released under the MIT License.
