import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import time
from simple import monitor_bike, cleanup

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

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/metrics':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            
            # Format metrics in Prometheus format
            metrics = [
                f'# HELP bike_rpm Revolutions per minute',
                f'# TYPE bike_rpm gauge',
                f'bike_rpm {current_metrics["rpm"]}',
                '',
                f'# HELP bike_distance_meters Total distance traveled (meters)',
                f'# TYPE bike_distance_meters gauge',
                f'bike_distance_meters {current_metrics["distance"]}',
                '',
                f'# HELP bike_calories Total estimated calories burned',
                f'# TYPE bike_calories gauge',
                f'bike_calories {current_metrics["calories"]}',
                '',
                f'# HELP bike_alarm_state Alarm state (0=off,1=on)',
                f'# TYPE bike_alarm_state gauge',
                f'bike_alarm_state {current_metrics["alarm_state"]}',
                '',
                f'# HELP bike_service_enabled Service state (0=off,1=on)',
                f'# TYPE bike_service_enabled gauge',
                f'bike_service_enabled {current_metrics["service_state"]}'
            ]
            
            self.wfile.write('\n'.join(metrics).encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')

    def log_message(self, format, *args):
        # Silence default logging
        return

class ServiceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global service_enabled, manual_toggle

        if self.path == '/service':
            # Toggle the service state manually
            service_enabled = not service_enabled
            current_metrics['service_state'] = 1 if service_enabled else 0
            manual_toggle = not service_enabled  # mark that the user manually disabled
            state = "enabled" if service_enabled else "disabled"
            print(f"Service {state} via GET /service.")
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {'service_enabled': service_enabled}
            self.wfile.write(str(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')

    def log_message(self, format, *args):
        # Silence default logging
        return

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
    while True:
        metrics = monitor_bike()
        
        if metrics:
            # Update metrics
            current_metrics['rpm'] = metrics.rpm
            current_metrics['distance'] = metrics.distance
            current_metrics['calories'] = metrics.calories
            
            # Handle service state
            if not service_enabled and metrics.is_moving:
                service_enabled = True
                current_metrics['service_state'] = 1
                manual_toggle = False
                print("Service auto-enabled on pedal activity.")
            
            # Handle alarm state
            if service_enabled and not metrics.is_moving:
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