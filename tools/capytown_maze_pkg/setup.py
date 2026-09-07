from setuptools import setup
import os
from glob import glob

package_name = "capytown_maze_pkg"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages",
         ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="NEXUS / Team SEAL",
    maintainer_email="team@seal.local",
    description="Reactive LiDAR+odometry wall-following maze solver (no camera).",
    license="MIT",
    entry_points={
        "console_scripts": [
            "maze_navigator = capytown_maze_pkg.maze_navigator:main",
            "box_detector = capytown_maze_pkg.box_detector:main",
            "lidar_visualizer = capytown_maze_pkg.lidar_visualizer:main",
        ],
    },
)
