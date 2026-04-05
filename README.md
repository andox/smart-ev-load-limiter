# Smart EV Load Limiter

Smart EV Load Limiter is a Home Assistant automation blueprint that continuously adjusts your EV charger's current limit to keep total household power below a configured cap. It is designed for setups such as Tibber plus DEFA, but it stays integration-agnostic by reading generic Home Assistant entities.

## Features
- Caps charging current based on available household power headroom
- Supports either whole-house power including EV load or a base-load sensor that already excludes EV charging
- Converts watts to charger amps using configurable phase count and voltage
- Respects a persistent user-defined preferred max amps value instead of learning from a possibly temporary throttled charger state, except when a non-zero-minimum charger would otherwise receive an invalid below-minimum setting
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
| **House max power** | Hard household power cap in watts. | `10500` |
| **Preferred max charging current** | User-selected max charger current while actively charging. Normally the limiter never exceeds this, but if it is set below the charger's effective non-zero minimum, the blueprint uses that minimum instead of sending an invalid value. | `16 A` |
| **Non-charging current limit** | Current limit restored when the limiter is inactive or the car is not charging. Use this as the safer fallback current when the blueprint is idle. | `16 A` |
| **Minimum charging current** | Lowest usable charging current. | `6 A` |
| **Stop charging below minimum** | If the safe target falls below the charger minimum and the charger cannot accept `0 A`, press the configured stop-charging button instead of clamping to the minimum. | `false` |
| **Stop charging button** | Optional button entity used for automatic stop charging. | Empty |
| **Start charging button** | Optional button entity used to resume charging after an automatic stop. | Empty |
| **Auto-stop tracking helper** | Optional `input_boolean` used to track automatic stop/start state reliably. Required if you enable automatic stop/start support. | Empty |
| **Stop/start cooldown** | Minimum wait time between automatic stop and start button presses. | `15 min` |
| **Stop delay below minimum** | How long the charger must stay pinned at the minimum current while headroom is still too low before the blueprint presses stop. | `5 min` |
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
2. If the limiter is outside its active schedule, or the car is not charging, it restores the charger to **Non-charging current limit** unless the blueprint is holding an auto-stopped session for a later safe restart.
3. If charging is active:
   - it reads the house power
   - in `whole_house_including_ev` mode it subtracts EV charging power to estimate non-EV base load
   - if an EV power sensor is available, it uses the actual live EV charging power
   - otherwise it estimates EV charging power from `current charger amps * phases * volts`
   - it computes remaining power headroom
   - it converts that headroom to amps with `amps = floor(watts / (phases * volts))`
   - it clamps the result to the configured preferred maximum, except that a charger with a non-zero minimum is clamped to that effective minimum if your preferred max is set lower than the charger can actually accept
   - it only applies a new charger limit when the change is large enough and the cooldown has elapsed
4. If the calculated amps fall below the configured minimum:
   - it sets `0 A` when the charger configuration allows zero
   - if **Stop charging below minimum** is enabled and you configured the stop/start helper, it can press the configured stop button instead of clamping to the minimum once the below-minimum condition has stayed true for the configured stop delay and the charger is still pinned at its minimum current
   - after an automatic stop, it keeps the session in that stopped state until headroom returns and then sets the safe target current before pressing the start button
   - otherwise it falls back to the configured minimum

The blueprint treats **Preferred max charging current** as the normal charging ceiling while a session is active. It does not learn a new baseline from the current DEFA number value, because that value may only be a temporary limiter result during an active charging session or after a Home Assistant restart. If you configure **Preferred max charging current** below the charger's effective non-zero minimum, the charger minimum wins because Home Assistant cannot safely apply a lower valid number.

Using a real EV power sensor is more accurate than estimating from the current charger limit. The estimate is still useful, but it assumes the car is drawing the full allowed current, which may not be true during ramp-up, near full battery, or when the vehicle reduces draw on its own.

## Manual Override Behavior
- Pressing the snooze button starts a timed bypass.
- Pressing the snooze button also immediately restores the charger to **Preferred max charging current** so the override actually gives you full normal charging.
- While snoozed, the blueprint stops writing to the charger current limit.
- Manual changes to the charger current limit from the Home Assistant UI are treated as a temporary override for the snooze duration, whether you raise or lower the current.
- When snooze expires, the blueprint resumes by recalculating from live sensor values instead of restoring a stale previously-limited amp value.

## Notifications
- When **Enable notifications** is on, the blueprint notifies only when it actively lowers the charger below both the current setting and your preferred max current.
- It does not notify on normal restores back to your configured charging or non-charging limits, upward adjustments while still limited, or while snoozed.
- It can send to selected Mobile App devices.
- It can also create a persistent Home Assistant notification.

## Debugging
- When the automation trace shows no action, open **Changed variables** on the last `choose` step and check: `sensor_values_available`, `snooze_active`, `schedule_active`, `is_charging`, `computed_target_amps`, `target_delta_amps`, `adjustment_threshold_met`, and `adjustment_cooldown_elapsed`.
- If `current_limit_amps` already equals `computed_target_amps`, the blueprint is intentionally doing nothing.
- If `snooze_active` is `true`, the limiter is bypassed either because the snooze button is still active or because a recent manual current change was treated as a temporary override.

## Notes
- If your charger or vehicle only uses one phase, set **Charging phase count** accordingly.
- If you want a safer fallback when the limiter is idle or something goes wrong, set **Non-charging current limit** lower than **Preferred max charging current**.
- Overnight schedules use the selected weekday as the window start day, so a Monday `22:00` to `06:00` window remains active until Tuesday `06:00`.
- If your charger cannot be set to `0`, enable **Stop charging below minimum** and configure the optional stop/start buttons together with **Auto-stop tracking helper** if you want the blueprint to stop the session instead of clamping to the minimum current. Use **Stop delay below minimum** to require the below-minimum condition to persist before stopping.
- While an auto-stopped session is waiting for restart, the blueprint intentionally does not restore **Non-charging current limit**. When headroom returns, it writes the computed safe current first and then presses the start button.
- The periodic recheck is also what resumes limiter control after a snooze expires if no relevant entity changes occur exactly at that moment.
- Automatic snooze from manual charger changes depends on Home Assistant receiving that charger-limit change with a user context, which is true for normal UI edits but may not be true for every third-party app path.
- The charger current entity is no longer used as a direct trigger, which reduces self-induced feedback loops after the blueprint changes the charger limit itself.
- The snooze button uses an `input_button.press` service-event trigger because an empty optional entity trigger can fail Home Assistant setup validation. As a result, unrelated input-button presses may still appear as harmless no-op traces for this automation.

## License
This project is released under the MIT License.
