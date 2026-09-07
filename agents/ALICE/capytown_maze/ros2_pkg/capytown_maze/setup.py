from setuptools import setup

package_name = 'capytown_maze'

setup(
    name=package_name,
    version='1.0.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/maze.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Henry / Team SEAL (ALICE)',
    maintainer_email='williamtovaru@gmail.com',
    description='Navegación de laberinto con LiDAR 2D + odometría (sin cámara) — Yahboom MicroROS-Pi5.',
    license='MIT',
    entry_points={
        'console_scripts': [
            'maze_solver = capytown_maze.maze_solver:main',
        ],
    },
)
