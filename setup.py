#!/usr/bin/env python
"""
VelocityCollector - Network Device Data Collection Tool

A PyQt6 desktop application for structured network device data collection.
Combines device inventory management, encrypted credential storage, and
job-based collection execution.
"""

import os
from setuptools import setup, find_packages

# Read the README for long description
here = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

# Version
VERSION = '0.3.2'

setup(
    name='velocitycollector',
    version=VERSION,
    description='Network device data collection tool with GUI and CLI',
    long_description=long_description,
    long_description_content_type='text/markdown',

    author='Scott Peterman',
    author_email='scottpeterman@gmail.com',
    url='https://github.com/scottpeterman/velocitycollector',

    license='MIT',

    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: X11 Applications :: Qt',
        'Environment :: Console',
        'Intended Audience :: System Administrators',
        'Intended Audience :: Information Technology',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Topic :: System :: Networking',
        'Topic :: System :: Systems Administration',
    ],

    keywords='network automation ssh collection netbox pyqt6 textfsm',

    packages=find_packages(exclude=['tests', 'tests.*', 'screenshots', 'jobs_v2']),

    python_requires='>=3.10',

    install_requires=[
        'click>=8.0',
        'paramiko>=3.0',
        'PyQt6>=6.4',
        'PyYAML>=6.0',
        'textfsm>=1.1',
        'cryptography>=41.0',
        'requests>=2.30.0'
    ],

    extras_require={
        'dev': [
            'invoke>=2.2',
            'pytest>=7.0',
            'pytest-qt>=4.0',
        ],
    },

    # Include package data (non-Python files)
    include_package_data=True,
    package_data={
        'vcollector': [
            'core/tfsm_templates.db',
        ],
    },

    # Entry points for CLI commands
    entry_points={
        'console_scripts': [
            'vcollector=vcollector.cli.main:main',
        ],
    },

    project_urls={
        'Bug Reports': 'https://github.com/scottpeterman/velocitycollector/issues',
        'Source': 'https://github.com/scottpeterman/velocitycollector',
    },
)