import time
import subprocess
import importlib.util
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from beep import Beeper
import RPi.GPIO as GPIO

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Check and install required packages
required_packages = ['RPi.GPIO']
for package in required_packages:
    if importlib.util.find_spec(package) is None:
        print(f"Installing {package}...")
        subprocess.check_call(['pip', 'install', package])

# Constants
HALL_SENSOR_PIN = 17  # GPIO pin connected to hall sensor
WHEEL_CIRCUMFERENCE = 2.105  # meters (26" wheel)
MIN_RPM_THRESHOLD = 5  # Minimum RPM to consider as pedaling
STOP_DETECTION_TIME = 2.0  # seconds to wait before considering stopped
MAX_WARNING_TIME = 180  # 3 minutes in seconds
THREAD_JOIN_TIMEOUT = 2.0  # seconds to wait for thread cleanup

class BikeMetrics:
    def __init__(self):
        self.last_pulse_time = None
        self.pulse_count = 0
        self.total_distance = 0.0  # meters
        self.current_rpm = 0.0
        self.last_rpm_update = time.time()
        self.is_pedaling = False
        self.last_pedaling_time = None
        self.beeper = Beeper()
        self.stop_warning_thread = None
        self.stop_warning_active = False
        self.calories = 0.0  # Add calories tracking
        self.service_enabled = True  # Start enabled
        self.warning_start_time = None
        self._lock = threading.Lock()  # Thread safety lock

    def reset_system(self):
        """Reset the system state when pedaling starts."""
        with self._lock:
            self.service_enabled = True
            self.stop_warning_active = False
            if self.stop_warning_thread:
                try:
                    self.stop_warning_thread.join(timeout=THREAD_JOIN_TIMEOUT)
                except Exception as e:
                    logger.error(f"Error joining warning thread: {e}")
            self.warning_start_time = None
            logger.info("System reset")

    def disable_service(self):
        """Disable the service."""
        with self._lock:
            self.service_enabled = False
            if self.stop_warning_active:
                self.stop_warning_active = False
                if self.stop_warning_thread:
                    try:
                        self.stop_warning_thread.join(timeout=THREAD_JOIN_TIMEOUT)
                    except Exception as e:
                        logger.error(f"Error joining warning thread: {e}")
            logger.info("Service disabled")

    def pulse_callback(self, channel):
        try:
            current_time = time.time()
            
            if self.last_pulse_time is not None:
                time_diff = current_time - self.last_pulse_time
                if time_diff > 0:
                    self.current_rpm = 60.0 / time_diff  # Convert to RPM
            
            self.last_pulse_time = current_time
            self.pulse_count += 1
            self.total_distance += WHEEL_CIRCUMFERENCE
            self.last_rpm_update = current_time
            
            # If we start pedaling, reset the system and play start beep
            if not self.is_pedaling:
                self.is_pedaling = True
                self.reset_system()
                self.beeper.short_beep()  # Acknowledge start with short beep
                logger.info("Pedaling started")
            
            self.last_pedaling_time = current_time
            
            # Update calories (rough estimate: 1 calorie per 10 meters)
            self.calories += WHEEL_CIRCUMFERENCE / 10.0
        except Exception as e:
            logger.error(f"Error in pulse callback: {e}")

    def check_pedaling_status(self):
        try:
            current_time = time.time()
            if self.last_rpm_update and (current_time - self.last_rpm_update) > STOP_DETECTION_TIME:
                if self.is_pedaling:
                    self.is_pedaling = False
                    # Play stop beep and start warning pattern if service is enabled
                    if self.service_enabled:
                        self.beeper.long_beep()  # Acknowledge stop with long beep
                        self.start_stop_warning()
                        logger.info("Pedaling stopped, warning started")
                self.current_rpm = 0.0
        except Exception as e:
            logger.error(f"Error checking pedaling status: {e}")

    def start_stop_warning(self):
        with self._lock:
            if not self.stop_warning_active and self.service_enabled:
                self.stop_warning_active = True
                self.warning_start_time = time.time()
                self.stop_warning_thread = threading.Thread(target=self._stop_warning_loop)
                self.stop_warning_thread.daemon = True
                self.stop_warning_thread.start()
                logger.info("Warning pattern started")

    def _stop_warning_loop(self):
        try:
            beep_count = 1
            
            while self.stop_warning_active and self.service_enabled:
                if not self.is_pedaling:  # Only continue if still not pedaling
                    # Check if we've exceeded the maximum warning time
                    if time.time() - self.warning_start_time > MAX_WARNING_TIME:
                        self.stop_warning_active = False
                        logger.info("Warning pattern timeout reached")
                        break
                        
                    # Play multiple short beeps based on time elapsed
                    for _ in range(beep_count):
                        self.beeper.short_beep()
                        time.sleep(0.2)
                    time.sleep(10 - (0.2 * beep_count))
                    beep_count += 1
                else:
                    self.stop_warning_active = False
                    logger.info("Warning pattern stopped - pedaling resumed")
                    break
        except Exception as e:
            logger.error(f"Error in warning loop: {e}")
            self.stop_warning_active = False

    def get_metrics(self):
        self.check_pedaling_status()
        return {
            'distance': self.total_distance / 1609.34,  # Convert to miles
            'rpm': self.current_rpm,
            'is_pedaling': self.is_pedaling,
            'calories': self.calories,
            'service_enabled': self.service_enabled
        }

    def cleanup(self):
        try:
            self.stop_warning_active = False
            if self.stop_warning_thread:
                self.stop_warning_thread.join(timeout=THREAD_JOIN_TIMEOUT)
            self.beeper.cleanup()
            GPIO.cleanup()
            logger.info("Cleanup completed")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/metrics':
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                
                metrics = bike_metrics.get_metrics()
                response = f"""# HELP bike_distance Total distance traveled in miles
# TYPE bike_distance gauge
bike_distance {metrics['distance']:.2f}

# HELP bike_rpm Current RPM
# TYPE bike_rpm gauge
bike_rpm {metrics['rpm']:.2f}

# HELP bike_pedaling Whether the bike is currently being pedaled
# TYPE bike_pedaling gauge
bike_pedaling {1 if metrics['is_pedaling'] else 0}

# HELP bike_calories Total estimated calories burned
# TYPE bike_calories gauge
bike_calories {metrics['calories']:.2f}

# HELP bike_service_enabled Whether the service is enabled
# TYPE bike_service_enabled gauge
bike_service_enabled {1 if metrics['service_enabled'] else 0}
"""
                self.wfile.write(response.encode())
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:
            logger.error(f"Error handling metrics request: {e}")
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence default logging
        return

class ServiceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/service':
                bike_metrics.disable_service()
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'Service disabled')
                logger.info("Service disabled via HTTP request")
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:
            logger.error(f"Error handling service request: {e}")
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence default logging
        return

def run_metrics_server():
    try:
        server_address = ('0.0.0.0', 8000)  # Bind to all interfaces
        httpd = HTTPServer(server_address, MetricsHandler)
        logger.info("Starting metrics server on port 8000...")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"Error in metrics server: {e}")

def run_service_server():
    try:
        server_address = ('0.0.0.0', 5000)  # Bind to all interfaces
        httpd = HTTPServer(server_address, ServiceHandler)
        logger.info("Starting service server on port 5000...")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"Error in service server: {e}")

if __name__ == "__main__":
    try:
        # Setup GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(HALL_SENSOR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        
        # Initialize metrics
        bike_metrics = BikeMetrics()
        GPIO.add_event_detect(HALL_SENSOR_PIN, GPIO.FALLING, callback=bike_metrics.pulse_callback)
        
        # Start metrics server in a separate thread
        metrics_thread = threading.Thread(target=run_metrics_server)
        metrics_thread.daemon = True
        metrics_thread.start()
        
        # Start service server in a separate thread
        service_thread = threading.Thread(target=run_service_server)
        service_thread.daemon = True
        service_thread.start()
        
        logger.info("System initialized and running")
        
        # Keep main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        bike_metrics.cleanup()