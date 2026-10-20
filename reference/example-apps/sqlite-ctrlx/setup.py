from setuptools import setup

setup(
      name="sqlite-ctrlx",
      version="2.0.05",
      description="Simple python powered SQLite database",
      author="A. Fouraker", 
      install_requires = ['ctrlx-datalayer'],   
      packages=['app','helper'],
      # https://stackoverflow.com/questions/1612733/including-non-python-files-with-setup-py
      scripts=['main.py'],
      license="Licensed under MIT License"
 )
