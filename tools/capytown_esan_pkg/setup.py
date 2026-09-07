import os
from glob import glob
from setuptools import setup

package_name = 'capytown_esan_pkg'

setup(
    name=package_name,
    version='0.1.0',
    packages=['capytown_esan'],
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Grupo 4 ESAN',
    maintainer_email='grupo4@esan.edu.pe',
    description='CapyTown RC-2 lane following (HSV + IPM + PID).',
    license='MIT',
    entry_points={
        'console_scripts': [
            'lane_detector = capytown_esan.lane_detector:main',
            'lane_controller = capytown_esan.lane_controller:main',
        ],
    },
)
