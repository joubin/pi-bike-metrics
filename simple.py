import subprocess
import sys
import importlib.util

def check_and_install_package(package_name):
    if importlib.util.find_spec(package_name) is None:
        print(f"Installing {package_name}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
        print(f"{package_name} installed successfully!")

# Check and install required packages
required_packages = ['RPi.GPIO']
for package in required_packages:
    check_and_install_package(package)

import RPi.GPIO as GPIO
import time
from dataclasses import dataclass
from typing import Optional

@dataclass
class BikeMetrics:
    timestamp: float
    rpm: float
    distance: float
    calories: float
    is_moving: bool

# CONFIGURATION
SENSOR_PIN = 17            # GPIO pin connected to sensor signal
WHEEL_CIRCUMFERENCE = 2.1  # meters per revolution (adjust to match your bike)
CALORIES_PER_REV = 0.12    # approximate calories per pedal revolution
MIN_RPM = 5.0             # Minimum RPM to consider as "moving"
STOP_THRESHOLD = 2.0      # Seconds without movement to consider stopped

# STATE VARIABLES
last_pulse_time = time.time()
revolutions = 0
total_distance = 0.0
total_calories = 0.0
rpm = 0.0
is_moving = False
last_update_time = time.time()

# GPIO SETUP
GPIO.setmode(GPIO.BCM)
GPIO.setup(SENSOR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def get_current_metrics() -> BikeMetrics:
    """Get current bike metrics with timestamp."""
    return BikeMetrics(
        timestamp=time.time(),
        rpm=rpm,
        distance=total_distance,
        calories=total_calories,
        is_moving=is_moving
    )

def on_pulse(channel):
    global last_pulse_time, revolutions, total_distance, total_calories, rpm, is_moving, last_update_time

    now = time.time()
    revolutions += 1
    total_distance = revolutions * WHEEL_CIRCUMFERENCE
    total_calories = revolutions * CALORIES_PER_REV

    if revolutions > 1:
        dt = now - last_pulse_time
        rpm = 60.0 / dt
    else:
        rpm = 0.0  # not enough data yet

    last_pulse_time = now
    last_update_time = now
    is_moving = True
    print(f"RPM: {rpm:.1f} | Distance: {total_distance:.2f} m | Calories: {total_calories:.1f}")

# Attach interrupt
GPIO.add_event_detect(SENSOR_PIN, GPIO.FALLING, callback=on_pulse, bouncetime=5)

def monitor_bike() -> Optional[BikeMetrics]:
    """Monitor bike and return metrics when they change."""
    global rpm, is_moving
    
    now = time.time()
    idle_duration = now - last_pulse_time

    # Check if we should consider the bike stopped
    if idle_duration > STOP_THRESHOLD and is_moving:
        print(f"Stopped pedaling. Final stats - Distance: {total_distance:.2f} m | Calories: {total_calories:.1f}")
        is_moving = False
        rpm = 0.0
        return get_current_metrics()

    # Calculate dynamic RPM decay only if we're moving
    if is_moving and idle_duration > 0:
        dynamic_rpm = 60.0 / idle_duration
        if dynamic_rpm < rpm:  # Only update if RPM is decreasing
            rpm = dynamic_rpm
            if rpm >= MIN_RPM:
                print(f"RPM: {rpm:.1f} | Distance: {total_distance:.2f} m | Calories: {total_calories:.1f}")
                return get_current_metrics()
            elif is_moving:  # Only print once when stopping
                print(f"Stopped pedaling. Final stats - Distance: {total_distance:.2f} m | Calories: {total_calories:.1f}")
                is_moving = False
                return get_current_metrics()
    
    return None

def cleanup():
    """Clean up GPIO resources."""
    GPIO.cleanup()

if __name__ == "__main__":
    print("Monitoring pedal sensor... Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(0.5)
            monitor_bike()
    except KeyboardInterrupt:
        print("\nExiting...")
        cleanup() 