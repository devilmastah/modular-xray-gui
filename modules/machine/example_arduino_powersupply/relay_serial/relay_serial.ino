/*
 * Relay serial – Arduino sketch for Example Arduino powersupply
 *
 * Listens on Serial (9600 baud) for line-based commands:
 *   ON   – set relay pin HIGH, wait BEAM_READY_DELAY_MS, then send "READY" (handshake)
 *   OFF  – set relay pin LOW, send "OK"
 *
 * Active high only: HIGH = relay on, LOW = relay off. Relay starts OFF at boot.
 * The delay + READY handshake lets the app wait for "beam ready" before starting acquisition.
 * Default: LED_BUILTIN (pin 13) for testing; change RELAY_PIN to 8 (or your relay pin) for a real relay.
 *
 * Open this folder in Arduino IDE: File → Open → relay_serial/relay_serial.ino
 */

#define SERIAL_BAUD 9600
#define RELAY_PIN LED_BUILTIN   // Built-in LED (pin 13) for testing; use e.g. 8 for a relay module
#define BEAM_READY_DELAY_MS 2000   // Delay after turning on before sending READY (e.g. relay/hardware settle)

void setup() {
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);   // Start with relay off
  Serial.begin(SERIAL_BAUD);
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    line.toUpperCase();
    if (line == "ON") {
      digitalWrite(RELAY_PIN, HIGH);
      delay(BEAM_READY_DELAY_MS);
      Serial.println("READY");
    } else if (line == "OFF") {
      digitalWrite(RELAY_PIN, LOW);
      Serial.println("OK");
    }
  }
}
