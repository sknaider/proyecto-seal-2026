#!/usr/bin/env python3
"""
Genera lane_error_s11.png a partir de un ros2 bag.
Uso:  python3 plot_lane_error.py <ruta_bag>
"""
import sys
import matplotlib
matplotlib.use('Agg')  # sin display (sirve por SSH)
import matplotlib.pyplot as plt
from rosbag2_py import SequentialReader, StorageOptions, ConverterOptions
from rclpy.serialization import deserialize_message
from std_msgs.msg import Float32

if len(sys.argv) < 2:
    print('Uso: python3 plot_lane_error.py <ruta_bag>')
    sys.exit(1)

reader = SequentialReader()
reader.open(StorageOptions(uri=sys.argv[1], storage_id='sqlite3'),
            ConverterOptions('', ''))

t0, ts, errs = None, [], []
while reader.has_next():
    topic, data, stamp = reader.read_next()
    if topic == '/lane_error':
        t0 = stamp if t0 is None else t0
        ts.append((stamp - t0) * 1e-9)
        errs.append(deserialize_message(data, Float32).data)

plt.plot(ts, errs)
plt.axhline(0, ls='--', c='gray')
plt.xlabel('tiempo (s)')
plt.ylabel('/lane_error (m)')
plt.title('Error lateral - 3 vueltas RC-2')
plt.grid(True)
plt.savefig('lane_error_s11.png', dpi=150)
print('Guardado lane_error_s11.png  (%d muestras)' % len(errs))
