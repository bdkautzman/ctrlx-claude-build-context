# SPDX-FileCopyrightText: Bosch Rexroth AG
#
# SPDX-License-Identifier: MIT
from setuptools import setup

setup(name='cam-builder',
      version='2.3.0',
      description='Simple web-based cam editor',
      author='SDK Team',
      install_requires=['cysystemd','PyJWT', 'ctrlx-datalayer<=3.5', 'ctrlx-fbs'],
      scripts=['main.py'],
      packages=['app', 'web'],
      license='MIT License'
      )
