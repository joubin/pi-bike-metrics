import RPi.GPIO as GPIO
import time

# CONFIGURATION
BUZZER_PIN = 18  # GPIO pin connected to buzzer
FREQUENCY = 440  # Hz (A4 note)

class Beeper:
    def __init__(self):
        # Setup GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BUZZER_PIN, GPIO.OUT)
        self.pwm = GPIO.PWM(BUZZER_PIN, FREQUENCY)
        self.pwm.start(0)  # Start with 0% duty cycle (silent)

    def long_beep(self):
        """Play a long beep (1 second)."""
        self.pwm.ChangeDutyCycle(50)  # 50% duty cycle
        time.sleep(1)
        self.pwm.ChangeDutyCycle(0)

    def short_beep(self):
        """Play a short beep (0.2 seconds)."""
        self.pwm.ChangeDutyCycle(50)  # 50% duty cycle
        time.sleep(0.2)
        self.pwm.ChangeDutyCycle(0)

    def cleanup(self):
        """Clean up GPIO resources."""
        try:
            self.pwm.ChangeDutyCycle(0)  # Stop PWM output
            self.pwm.stop()              # Stop PWM
            GPIO.output(BUZZER_PIN, GPIO.LOW)  # Ensure pin is low
            GPIO.cleanup(BUZZER_PIN)     # Clean up specific pin
        except Exception as e:
            print(f"Cleanup error: {e}")

if __name__ == "__main__":
    beeper = Beeper()
    try:
        print("Testing beeps...")
        print("Long beep:")
        beeper.long_beep()
        time.sleep(1)
        
        print("Short beep:")
        beeper.short_beep()
        time.sleep(1)
        
    finally:
        beeper.cleanup() 