#!/bin/bash
# Example of how the MAS controller connection information is set up

# In the target environment the TCP connection information is defined in a shell
# script like this one. The correct MAS controller for each spectrometer is 
# selected based on environment variables that are set up when logging into the
# spectrometer computer. To manually specify a controller make sure that the
# RNMR_SPECIFIC environment variable is not set. Replace these example values
# with the actual name/address and port to connect to the target MAS controller.

export TRM1_TCP_NODE="name_to_connect_to_mas_controller"
export TRM1_TCP_PORT="10002"
