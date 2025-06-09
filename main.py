import time
import subprocess
import importlib.util
import threading
import logging
from logging.handlers import RotatingFileHandler
from http.server import HTTPServer, BaseHTTPRequestHandler
from beep import Beeper
import RPi.GPIO as GPIO

# Configure logging
log_file = '/tmp/bikeos.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Console handler
        RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=1)  # 10MB file size limit
    ]
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
        
        # System metrics
        self.system_start_time = time.time()
        self.last_metrics_update = time.time()
        self.total_pedaling_time = 0.0
        self.total_idle_time = 0.0
        self.total_warning_time = 0.0
        self.warning_count = 0
        self.service_disable_count = 0
        self.last_service_disable_time = None
        self.peak_rpm = 0.0
        self.peak_speed = 0.0  # km/h
        self.total_pulses = 0
        self.error_count = 0
        
        # Metrics update intervals (in seconds)
        self.ACTIVE_UPDATE_INTERVAL = 1.0  # Update every second when active
        self.DISABLED_UPDATE_INTERVAL = 5.0  # Update every 5 seconds when disabled
        self.last_metrics_publish = time.time()

    def reset_system(self):
        """Reset the system state when pedaling starts."""
        with self._lock:
            # Only re-enable if we were previously disabled
            if not self.service_enabled:
                self.service_enabled = True
                logger.info("Service re-enabled due to pedaling")
            self.stop_warning_active = False
            if self.stop_warning_thread:
                try:
                    self.stop_warning_thread.join(timeout=THREAD_JOIN_TIMEOUT)
                except Exception as e:
                    logger.error(f"Error joining warning thread: {e}")
                    self.error_count += 1
            self.warning_start_time = None
            logger.info("System reset")

    def disable_service(self):
        """Disable the service."""
        with self._lock:
            self.service_enabled = False
            self.service_disable_count += 1
            self.last_service_disable_time = time.time()
            # Stop any active warnings
            if self.stop_warning_active:
                self.stop_warning_active = False
                if self.stop_warning_thread:
                    try:
                        self.stop_warning_thread.join(timeout=THREAD_JOIN_TIMEOUT)
                    except Exception as e:
                        logger.error(f"Error joining warning thread: {e}")
                        self.error_count += 1
            logger.info("Service disabled")

    def pulse_callback(self, channel):
        try:
            current_time = time.time()
            
            if self.last_pulse_time is not None:
                time_diff = current_time - self.last_pulse_time
                if time_diff > 0:
                    self.current_rpm = 60.0 / time_diff  # Convert to RPM
                    # Update peak RPM if current RPM is higher
                    if self.current_rpm > self.peak_rpm:
                        self.peak_rpm = self.current_rpm
                    
                    # Calculate speed in km/h
                    speed = (WHEEL_CIRCUMFERENCE * self.current_rpm * 60) / 1000
                    if speed > self.peak_speed:
                        self.peak_speed = speed
            
            self.last_pulse_time = current_time
            self.pulse_count += 1
            self.total_pulses += 1
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
            self.error_count += 1

    def check_pedaling_status(self):
        try:
            current_time = time.time()
            if self.last_rpm_update and (current_time - self.last_rpm_update) > STOP_DETECTION_TIME:
                if self.is_pedaling:
                    self.is_pedaling = False
                    # Update pedaling time
                    if self.last_pedaling_time:
                        self.total_pedaling_time += current_time - self.last_pedaling_time
                    # Play stop beep and start warning pattern if service is enabled
                    if self.service_enabled:
                        self.beeper.long_beep()  # Acknowledge stop with long beep
                        self.start_stop_warning()
                        logger.info("Pedaling stopped, warning started")
                    else:
                        logger.info("Pedaling stopped, but service is disabled - no warnings")
                self.current_rpm = 0.0
            elif self.is_pedaling:
                # Update pedaling time
                if self.last_pedaling_time:
                    self.total_pedaling_time += current_time - self.last_pedaling_time
                    self.last_pedaling_time = current_time
        except Exception as e:
            logger.error(f"Error checking pedaling status: {e}")
            self.error_count += 1

    def start_stop_warning(self):
        with self._lock:
            if not self.stop_warning_active and self.service_enabled:
                self.stop_warning_active = True
                self.warning_start_time = time.time()
                self.warning_count += 1
                self.stop_warning_thread = threading.Thread(target=self._stop_warning_loop)
                self.stop_warning_thread.daemon = True
                self.stop_warning_thread.start()
                logger.info("Warning pattern started")
            elif not self.service_enabled:
                logger.info("Warning not started - service is disabled")

    def _stop_warning_loop(self):
        try:
            beep_count = 1
            warning_start = time.time()
            
            while self.stop_warning_active and self.service_enabled:
                if not self.is_pedaling:  # Only continue if still not pedaling
                    # Check if we've exceeded the maximum warning time
                    if time.time() - self.warning_start_time > MAX_WARNING_TIME:
                        self.stop_warning_active = False
                        logger.info("Warning pattern timeout reached")
                        break
                        
                    # Play multiple short beeps based on time elapsed
                    for _ in range(beep_count):
                        if not self.service_enabled:  # Check service state before each beep
                            logger.info("Warning stopped - service disabled")
                            self.stop_warning_active = False
                            break
                        self.beeper.short_beep()
                        time.sleep(0.2)
                    
                    # Only sleep if we're still active
                    if self.stop_warning_active and self.service_enabled:
                        time.sleep(10 - (0.2 * beep_count))
                        beep_count += 1
                else:
                    self.stop_warning_active = False
                    logger.info("Warning pattern stopped - pedaling resumed")
                    break
            
            # Update total warning time
            self.total_warning_time += time.time() - warning_start
        except Exception as e:
            logger.error(f"Error in warning loop: {e}")
            self.error_count += 1
            self.stop_warning_active = False

    def should_update_metrics(self):
        """Determine if metrics should be updated based on service state."""
        current_time = time.time()
        interval = self.ACTIVE_UPDATE_INTERVAL if self.service_enabled else self.DISABLED_UPDATE_INTERVAL
        return (current_time - self.last_metrics_publish) >= interval

    def get_metrics(self):
        current_time = time.time()
        
        # Only update metrics if enough time has passed
        if self.should_update_metrics():
            self.check_pedaling_status()
            
            # Update idle time if not pedaling
            if not self.is_pedaling and self.last_pedaling_time:
                self.total_idle_time += current_time - self.last_pedaling_time
                self.last_pedaling_time = current_time
            
            # Update last metrics update time
            self.last_metrics_update = current_time
            self.last_metrics_publish = current_time
        
        return {
            'distance': self.total_distance / 1609.34,  # Convert to miles
            'rpm': self.current_rpm,
            'is_pedaling': self.is_pedaling,
            'calories': self.calories,
            'service_enabled': self.service_enabled,
            'system_uptime': current_time - self.system_start_time,
            'total_pedaling_time': self.total_pedaling_time,
            'total_idle_time': self.total_idle_time,
            'total_warning_time': self.total_warning_time,
            'warning_count': self.warning_count,
            'service_disable_count': self.service_disable_count,
            'peak_rpm': self.peak_rpm,
            'peak_speed': self.peak_speed,
            'total_pulses': self.total_pulses,
            'error_count': self.error_count,
            'last_service_disable_seconds': (current_time - self.last_service_disable_time) if self.last_service_disable_time else 0,
            'metrics_update_interval': self.ACTIVE_UPDATE_INTERVAL if self.service_enabled else self.DISABLED_UPDATE_INTERVAL
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

# HELP bike_system_uptime System uptime in seconds
# TYPE bike_system_uptime gauge
bike_system_uptime {metrics['system_uptime']:.2f}

# HELP bike_total_pedaling_time Total time spent pedaling in seconds
# TYPE bike_total_pedaling_time gauge
bike_total_pedaling_time {metrics['total_pedaling_time']:.2f}

# HELP bike_total_idle_time Total time spent idle in seconds
# TYPE bike_total_idle_time gauge
bike_total_idle_time {metrics['total_idle_time']:.2f}

# HELP bike_total_warning_time Total time spent in warning state in seconds
# TYPE bike_total_warning_time gauge
bike_total_warning_time {metrics['total_warning_time']:.2f}

# HELP bike_warning_count Total number of warning events
# TYPE bike_warning_count counter
bike_warning_count {metrics['warning_count']}

# HELP bike_service_disable_count Total number of service disable events
# TYPE bike_service_disable_count counter
bike_service_disable_count {metrics['service_disable_count']}

# HELP bike_peak_rpm Highest recorded RPM
# TYPE bike_peak_rpm gauge
bike_peak_rpm {metrics['peak_rpm']:.2f}

# HELP bike_peak_speed Highest recorded speed in km/h
# TYPE bike_peak_speed gauge
bike_peak_speed {metrics['peak_speed']:.2f}

# HELP bike_total_pulses Total number of hall sensor pulses
# TYPE bike_total_pulses counter
bike_total_pulses {metrics['total_pulses']}

# HELP bike_error_count Total number of errors encountered
# TYPE bike_error_count counter
bike_error_count {metrics['error_count']}

# HELP bike_last_service_disable_seconds Seconds since last service disable
# TYPE bike_last_service_disable_seconds gauge
bike_last_service_disable_seconds {metrics['last_service_disable_seconds']:.2f}

# HELP bike_metrics_update_interval Current metrics update interval in seconds
# TYPE bike_metrics_update_interval gauge
bike_metrics_update_interval {metrics['metrics_update_interval']}
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

class LogHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/logs':
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                
                try:
                    with open(log_file, 'r') as f:
                        log_content = f.read()
                    self.wfile.write(log_content.encode())
                except FileNotFoundError:
                    self.wfile.write(b'Log file not found')
                except Exception as e:
                    self.wfile.write(f'Error reading log file: {str(e)}'.encode())
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:
            logger.error(f"Error handling log request: {e}")
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

def run_log_server():
    try:
        server_address = ('0.0.0.0', 8001)  # Bind to all interfaces
        httpd = HTTPServer(server_address, LogHandler)
        logger.info("Starting log server on port 8001...")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"Error in log server: {e}")

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

        # Start log server in a separate thread
        log_thread = threading.Thread(target=run_log_server)
        log_thread.daemon = True
        log_thread.start()
        
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