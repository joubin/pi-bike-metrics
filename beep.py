import RPi.GPIO as GPIO
import time
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# CONFIGURATION
BUZZER_PIN = 18  # GPIO pin connected to buzzer
FREQUENCY = 440  # Hz (A4 note)

class Beeper:
    def __init__(self):
        try:
            # Setup GPIO
            GPIO.setmode(GPIO.BCM)
            self.buzzer_pin = BUZZER_PIN  # GPIO pin for buzzer
            GPIO.setup(self.buzzer_pin, GPIO.OUT)
            self.pwm = GPIO.PWM(self.buzzer_pin, FREQUENCY)  # 440Hz frequency
            self.pwm.start(50)  # 50% duty cycle
            self.pwm.ChangeDutyCycle(0)  # Start silent
            logger.info("Beeper initialized")
        except Exception as e:
            logger.error(f"Error initializing beeper: {e}")
            raise

    def _beep(self, duration):
        """Play a beep for the specified duration."""
        try:
            self.pwm.ChangeDutyCycle(50)  # 50% duty cycle
            time.sleep(duration)
            self.pwm.ChangeDutyCycle(0)  # Stop beeping
        except Exception as e:
            logger.error(f"Error during beep: {e}")

    def _silence(self, duration):
        """Maintain silence for the specified duration."""
        try:
            self.pwm.ChangeDutyCycle(0)
            time.sleep(duration)
        except Exception as e:
            logger.error(f"Error during silence: {e}")

    def short_beep(self):
        """Play a short beep."""
        try:
            self._beep(0.1)  # 100ms beep
            logger.debug("Short beep played")
        except Exception as e:
            logger.error(f"Error playing short beep: {e}")

    def long_beep(self):
        """Play a long beep."""
        try:
            self._beep(0.5)  # 500ms beep
            logger.debug("Long beep played")
        except Exception as e:
            logger.error(f"Error playing long beep: {e}")

    def cleanup(self):
        """Clean up GPIO resources."""
        try:
            self.pwm.stop()
            GPIO.cleanup(self.buzzer_pin)
            logger.info("Beeper cleanup completed")
        except Exception as e:
            logger.error(f"Error during beeper cleanup: {e}")

if __name__ == "__main__":
    try:
        # Test the beeper
        beeper = Beeper()
        
        print("Testing short beep...")
        beeper.short_beep()
        time.sleep(1)
        
        print("Testing long beep...")
        beeper.long_beep()
        time.sleep(1)
        
        print("Testing multiple beeps...")
        for _ in range(3):
            beeper.short_beep()
            time.sleep(0.2)
        
        print("Tests completed")
    except Exception as e:
        logger.error(f"Error during beeper test: {e}")
    finally:
        beeper.cleanup() 