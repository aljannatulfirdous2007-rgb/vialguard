# Wiring

Everything plugs into a breadboard. No soldering needed.

```
                 ESP32 DevKit V1
               ┌─────────────────┐
   3V3 ────────┤ 3V3         GND ├──────── GND rail
               │                 │
 DS18B20 DATA ─┤ GPIO 4          │
               │                 │
   LDR node ───┤ GPIO 34         │
               │                 │
  Buzzer + ────┤ GPIO 25         │
               │                 │
  OLED SDA ────┤ GPIO 21         │
  OLED SCL ────┤ GPIO 22         │
               │      [BOOT] = GPIO 0 (built-in button)
               └─────────────────┘
```

## 1. DS18B20 temperature probe (waterproof, 3 wires)

| Probe wire | Connect to |
|---|---|
| Red (VCC) | 3V3 |
| Black (GND) | GND |
| Yellow (DATA) | GPIO 4 |

Put a **4.7 kΩ resistor between DATA (GPIO 4) and 3V3**. Without it the sensor reads -127 °C.
Many probe "modules" already include this resistor on a small board.

## 2. OLED 0.96" (I2C, 4 pins)

| OLED pin | Connect to |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SDA | GPIO 21 |
| SCL | GPIO 22 |

If the screen stays blank, the address may be 0x3D: change `OLED_ADDR` in `config.h`.

## 3. LDR lid sensor (light = lid open)

```
3V3 ── LDR ──┬── GPIO 34
             │
           10 kΩ
             │
            GND
```

Light raises the voltage on GPIO 34. Watch the `ldr` value on the dashboard with the lid
closed and open, then set `LID_OPEN_ADC` in `config.h` halfway between the two.

## 4. Buzzer (active buzzer module)

| Buzzer pin | Connect to |
|---|---|
| + (or I/O) | GPIO 25 |
| − | GND |

## 5. Power

Power the ESP32 from a laptop USB port or a power bank. Some power banks switch off
when the current is very low: test yours for 30 minutes before demo day.

## Packaging for the demo

- Put the electronics in a small sealed box, and only the **waterproof probe** inside the
  insulated "medicine box" next to the insulin or vaccine carton.
- Glue the LDR to the inside of the medicine box lid area so opening the lid lets light in.
- Hot-glue or tape every jumper wire once it works. Loose wires are the #1 cause of demo failures.
