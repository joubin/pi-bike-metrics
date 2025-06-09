import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import time
from simple import monitor_bike, cleanup
import subprocess
import importlib.util
from beep import Beeper

# Check and install required packages
required_packages = ['RPi.GPIO']
for package in required_packages:
    if importlib.util.find_spec(package) is None:
        print(f"Installing {package}...")
        subprocess.check_call(['pip', 'install', package])

import RPi.GPIO as GPIO

# CONFIGURATION
METRICS_PORT = 8000     # port to serve metrics
HTTP_SERVER_PORT = 5000    # port to serve REST API
IDLE_TIMEOUT = 5           # seconds after which beeping disables gracefully

# STATE VARIABLES
service_enabled = False  # Controls beeping
manual_toggle = False    # Tracks if service was manually toggled off
current_metrics = {
    'rpm': 0.0,
    'distance': 0.0,
    'calories': 0.0,
    'alarm_state': 0,
    'service_state': 0
}

# Constants
HALL_SENSOR_PIN = 17  # GPIO pin connected to hall sensor
WHEEL_CIRCUMFERENCE = 2.105  # meters (26" wheel)
MIN_RPM_THRESHOLD = 5  # Minimum RPM to consider as pedaling
STOP_DETECTION_TIME = 2.0  # seconds to wait before considering stopped
EARLY_STOP_THRESHOLD = 1.0  # miles threshold for early stop warning

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
        self.service_enabled = False  # Track service state

    def set_service_state(self, enabled):
        """Update service state and stop beeping if service is disabled."""
        self.service_enabled = enabled
        if not enabled and self.stop_warning_active:
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
        
        # If we start pedaling, enable the service
        if not self.is_pedaling:
            self.is_pedaling = True
            self.service_enabled = True  # Auto-enable service when pedaling starts
        
        self.last_pedaling_time = current_time
        
        # Update calories (rough estimate: 1 calorie per 10 meters)
        self.calories += WHEEL_CIRCUMFERENCE / 10.0

    def check_pedaling_status(self):
        current_time = time.time()
        if self.last_rpm_update and (current_time - self.last_rpm_update) > STOP_DETECTION_TIME:
            if self.is_pedaling:
                self.is_pedaling = False
                # Check if we're stopping early (before 1 mile) and service is enabled
                if self.service_enabled and self.total_distance < (EARLY_STOP_THRESHOLD * 1609.34):
                    self.start_stop_warning()
            self.current_rpm = 0.0

    def start_stop_warning(self):
        if not self.stop_warning_active and self.service_enabled:
            self.stop_warning_active = True
            self.stop_warning_thread = threading.Thread(target=self._stop_warning_loop)
            self.stop_warning_thread.daemon = True
            self.stop_warning_thread.start()

    def _stop_warning_loop(self):
        start_time = time.time()
        beep_count = 1
        max_time = 300  # 5 minutes in seconds
        
        while self.stop_warning_active and self.service_enabled and (time.time() - start_time) < max_time:
            if not self.is_pedaling:  # Only continue if still not pedaling
                # Play initial long beep
                if beep_count == 1:
                    self.beeper.long_beep()
                    time.sleep(10)
                    beep_count += 1
                else:
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

class ServiceHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/service':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            
            if post_data == 'enable':
                bike_metrics.set_service_state(True)
            elif post_data == 'disable':
                bike_metrics.set_service_state(False)
            
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def run_metrics_server():
    server = HTTPServer(('', METRICS_PORT), MetricsHandler)
    print(f"Metrics server running at :{METRICS_PORT}/metrics")
    server.serve_forever()

def run_service_server():
    server = HTTPServer(('', HTTP_SERVER_PORT), ServiceHandler)
    print(f"Service server running at :{HTTP_SERVER_PORT}/service")
    server.serve_forever()

# Start metrics server in a separate thread
metrics_thread = threading.Thread(target=run_metrics_server)
metrics_thread.daemon = True
metrics_thread.start()

# Start service server in a separate thread
service_thread = threading.Thread(target=run_service_server)
service_thread.daemon = True
service_thread.start()

print(f"Monitoring pedal sensor... Metrics at :{METRICS_PORT}/metrics. Service at :{HTTP_SERVER_PORT}/service. Press Ctrl+C to exit.")

try:
    # Setup GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(HALL_SENSOR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    # Initialize metrics
    bike_metrics = BikeMetrics()
    GPIO.add_event_detect(HALL_SENSOR_PIN, GPIO.FALLING, callback=bike_metrics.pulse_callback)
    
    while True:
        metrics = bike_metrics.get_metrics()
        
        if metrics:
            # Update metrics
            current_metrics['rpm'] = metrics['rpm']
            current_metrics['distance'] = metrics['distance']
            current_metrics['calories'] = metrics['calories']
            
            # Handle service state
            if not service_enabled and metrics['is_pedaling']:
                service_enabled = True
                current_metrics['service_state'] = 1
                manual_toggle = False
                print("Service auto-enabled on pedal activity.")
            
            # Handle alarm state
            if service_enabled and not metrics['is_pedaling']:
                current_metrics['alarm_state'] = 1  # Alarm is active
                if not manual_toggle:
                    service_enabled = False
                    current_metrics['service_state'] = 0
                    print("Service auto-disabled due to inactivity.")
            else:
                current_metrics['alarm_state'] = 0

except KeyboardInterrupt:
    print("Exiting...")
    cleanup()