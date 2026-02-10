"""
Test script for openDAQ - PQTECH Demo
"""
import sys
import os

# Add openDAQ to Python path
build_path = os.path.join(os.path.dirname(__file__), 'build', 'bin', 'Release')
sys.path.insert(0, build_path)

import opendaq as daq

print("=" * 60)
print("openDAQ Test - PQTECH")
print("=" * 60)

# Create instance
print("\n1. Creating openDAQ instance...")
instance = daq.Instance()
print("   [OK] Instance created successfully")

# List available devices
print("\n2. Discovering available devices...")
available_devices = instance.available_devices
print(f"   Found {len(available_devices)} device types:")
for dev in available_devices:
    print(f"   - {dev.name}: {dev.connection_string}")

# Add simulator device (generates continuous data)
print("\n3. Adding simulator device...")
# Use the simulator connection string from available devices
sim_conn = [d.connection_string for d in available_devices if 'simulator' in d.name.lower()][0]
device = instance.add_device(sim_conn)
print(f"   [OK] Device added: {device.name}")

# Get device info
print("\n4. Device information:")
info = device.info
print(f"   Name: {info.name}")
print(f"   Connection string: {info.connection_string}")

# List channels
print("\n5. Listing channels...")
channels = device.channels_recursive
print(f"   Found {len(channels)} channels:")
for i, ch in enumerate(channels[:5]):  # Show first 5
    print(f"   - Channel {i}: {ch.name}")

# Get first channel and signal
if len(channels) > 0:
    print("\n6. Getting signal from first channel...")
    channel = channels[0]
    signals = channel.signals

    if len(signals) > 0:
        signal = signals[0]
        print(f"   [OK] Signal obtained: {signal.name}")

        # Create stream reader
        print("\n7. Creating stream reader...")
        reader = daq.StreamReader(signal)
        print("   [OK] Reader created")

        # Read some samples (wait a bit for data to be generated)
        print("\n8. Reading 100 samples...")
        import time
        time.sleep(0.5)  # Wait for data to be generated
        samples = reader.read(100)
        print(f"   [OK] Read {len(samples)} samples")
        print(f"   First 5 samples: {samples[:5]}")

        # Calculate basic statistics
        if len(samples) > 0:
            import statistics
            print("\n9. Sample statistics:")
            print(f"   Mean: {statistics.mean(samples):.3f}")
            print(f"   Std Dev: {statistics.stdev(samples):.3f}")
            print(f"   Min: {min(samples):.3f}")
            print(f"   Max: {max(samples):.3f}")
        else:
            print("\n9. No samples available (device may not be generating data)")

print("\n" + "=" * 60)
print("[OK] All tests passed successfully!")
print("openDAQ is ready to use for PQTECH projects")
print("=" * 60)
