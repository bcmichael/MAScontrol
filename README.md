This program allows for remote monitoring and operation of a Bruker MAS
controller. The gui allows for both manual and automatic control of spinning and
displays a real time graph of the spinning frequency. The spinning frequency
data can also be logged to a file on disk for later plotting and review.

The program is designed to run on the computers controlling the spectrometers of
the Griffin group at MIT and relies on various aspects of that specific
environment to function. In the absence of those environment variables the
symbols.sh and cfgmas.dat files provided here will be used. The local symbols.sh
must be edited to contain the correct TCP information for the desired MAS
controller for this approach to work.

'''
usage: mas.py [-h] [-l LOG_DIR] [-o]

optional arguments:
  -h, --help            show this help message and exit
  -l LOG_DIR, --log_dir LOG_DIR
                        Set the directory to save spinning logs to
  -o, --offline         Run offline using simple fake spinning values
'''