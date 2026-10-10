# Demo script (90 seconds)

**Who:** Person A runs the hardware, Person B talks. Dashboard on the laptop, Telegram open on a phone
handed to the judge.

**Before judges arrive:** device in the cold box at 5 °C, dashboard showing "Safe to use", drug = Demo biologic,
alert ladder cleared (press "New shipment").

| Time | Action (Person A) | What to say (Person B) |
|---|---|---|
| 0:00 | Point to the box | "This is insulin in a cold box. If a truck breaks down, it looks exactly the same afterwards, but it may no longer work. Today's loggers only record the temperature." |
| 0:15 | Hair dryer on the probe | "Watch: VialGuard turns heat into the medicine's remaining life, using the Arrhenius equation that pharma stability studies use." |
| 0:25 | Point at gauge dropping | "We run time 360× faster so you can see 3 hours of a broken-down truck in 30 seconds." |
| 0:30 | Judge's phone buzzes | "The courier just got an alert. If nobody acknowledges, it escalates to the pharmacist, then the QA head." |
| 0:45 | Hair dryer off, probe back in cold | "Back in range, but the lost life never comes back: read the % lost off the gauge. The verdict tells the pharmacist exactly how long it stays usable." |
| 0:55 | Open and close the lid | "Every lid opening is logged, so you know who broke the chain." |
| 1:05 | Ice pack on the probe | "Now the real killer: freezing. Heat damage adds up slowly; freezing destroys vaccines and insulin instantly." |
| 1:15 | Screen turns red: DISCARD: frozen | "Instant DISCARD. In a 10-state Indian study, 2 out of 3 vaccine vials showed freeze damage." |
| 1:25 | Point at "Verified" card | "And the record is hash-chained: if anyone edits a reading to hide this, the dashboard shows TAMPERED." |

**Closing line:** "Every cold-chain device tells you the temperature. VialGuard tells you if the medicine still works, for ₹990."

## Likely judge questions

- **How accurate is the prediction?** It uses standard Arrhenius kinetics and Mean Kinetic Temperature
  (USP 1079.2). Our drug profiles are illustrative; for deployment we would calibrate them from each
  manufacturer's published stability data. The VVM profiles follow WHO vaccine vial monitor classes.
- **How is this different from LogTag / Controlant / VVM stickers?** Loggers record data you read later.
  Controlant is real-time but priced for big pharma. VVM stickers only show heat, cannot detect
  freezing, and send no alert. VialGuard gives a live usability verdict, detects freezing,
  keeps an audit trail and costs about ₹990.
- **What if there is no network?** It keeps logging, buffers readings on the device and syncs later.
  It even runs with no internet at all, using the laptop as the server (`simulator/local_server.py`).
- **Why speed up time?** Only for the demo. `TIME_SCALE 1` in `config.h` is real time.
- **Battery life?** The prototype runs from USB. The next version uses deep sleep between readings and a coin cell.

## If something fails live

1. Hardware dead → run `python simulator/simulate.py --local` and do the same demo by typing
   `h` (heat), `c` (cold), `o` (lid), `f` (freeze).
2. No internet → laptop hotspot + local server (see README, "No internet").
3. Everything dead → play the backup video. **Record it the night before.**
