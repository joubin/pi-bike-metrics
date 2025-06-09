import time
import subprocess
import importlib.util
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from beep import Beeper

# Check and install required packages
required_packages = ['RPi.GPIO']
for package in required_packages:
    if importlib.util.find_spec(package) is None:
        print(f"Installing {package}...")
        subprocess.check_call(['pip', 'install', package])

import RPi.GPIO as GPIO

# Constants
HALL_SENSOR_PIN = 17  # GPIO pin connected to hall sensor
WHEEL_CIRCUMFERENCE = 2.105  # meters (26" wheel)
MIN_RPM_THRESHOLD = 5  # Minimum RPM to consider as pedaling
STOP_DETECTION_TIME = 2.0  # seconds to wait before considering stopped
MAX_WARNING_TIME = 180  # 3 minutes in seconds

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

    def reset_system(self):
        """Reset the system state when pedaling starts."""
        self.service_enabled = True
        self.stop_warning_active = False
        if self.stop_warning_thread:
            self.stop_warning_thread.join(timeout=1.0)
        self.warning_start_time = None

    def disable_service(self):
        """Disable the service."""
        self.service_enabled = False
        if self.stop_warning_active:
            self.stop_warning_active = False
            if self.stop_warning_thread:
                self.stop_warning_thread.join(timeout=1.0)

    def pulse_callback(self, channel):
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
        
        self.last_pedaling_time = current_time
        
        # Update calories (rough estimate: 1 calorie per 10 meters)
        self.calories += WHEEL_CIRCUMFERENCE / 10.0

    def check_pedaling_status(self):
        current_time = time.time()
        if self.last_rpm_update and (current_time - self.last_rpm_update) > STOP_DETECTION_TIME:
            if self.is_pedaling:
                self.is_pedaling = False
                # Play stop beep and start warning pattern if service is enabled
                if self.service_enabled:
                    self.beeper.long_beep()  # Acknowledge stop with long beep
                    self.start_stop_warning()
            self.current_rpm = 0.0

    def start_stop_warning(self):
        if not self.stop_warning_active and self.service_enabled:
            self.stop_warning_active = True
            self.warning_start_time = time.time()
            self.stop_warning_thread = threading.Thread(target=self._stop_warning_loop)
            self.stop_warning_thread.daemon = True
            self.stop_warning_thread.start()

    def _stop_warning_loop(self):
        beep_count = 1
        
        while self.stop_warning_active and self.service_enabled:
            if not self.is_pedaling:  # Only continue if still not pedaling
                # Check if we've exceeded the maximum warning time
                if time.time() - self.warning_start_time > MAX_WARNING_TIME:
                    self.stop_warning_active = False
                    break
                    
                # Play multiple short beeps based on time elapsed
                for _ in range(beep_count):
                    self.beeper.short_beep()
                    time.sleep(0.2)
                time.sleep(10 - (0.2 * beep_count))
                beep_count += 1
            else:
                self.stop_warning_active = False
                break

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
        self.stop_warning_active = False
        if self.stop_warning_thread:
            self.stop_warning_thread.join(timeout=1.0)
        self.beeper.cleanup()
        GPIO.cleanup()

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
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

    def log_message(self, format, *args):
        # Silence default logging
        return

class ServiceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/service':
            bike_metrics.disable_service()
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Service disabled')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence default logging
        return

def run_metrics_server():
    server_address = ('0.0.0.0', 8000)  # Bind to all interfaces
    httpd = HTTPServer(server_address, MetricsHandler)
    print("Starting metrics server on port 8000...")
    httpd.serve_forever()

def run_service_server():
    server_address = ('0.0.0.0', 5000)  # Bind to all interfaces
    httpd = HTTPServer(server_address, ServiceHandler)
    print("Starting service server on port 5000...")
    httpd.serve_forever()

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
        
        # Keep main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        bike_metrics.cleanup()