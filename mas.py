#!/usr/bin/python2

from __future__ import print_function, division
import os
import time
import Queue
import socket
import sys
from PyQt4 import QtGui, QtCore
from collections import namedtuple
import matplotlib
import matplotlib.animation
import matplotlib.dates
from matplotlib.backends.backend_qt4agg import FigureCanvasQTAgg
import numpy as np
import itertools
from datetime import datetime, timedelta
import shlex
import argparse

MASStatus = namedtuple('MASStatus', 'spin, drive, bearing, sense, spin_set')

class MASView(QtGui.QWidget):
    """Window to control and view MAS controller operation.

    Args:
        parent: main QApplication running the program
        config: Configuration object to set up spin history plotting
        log_dir: Optional directory name to store spinning log files
    """

    pressure_digits = 4
    spin_digits = 5
    status = MASStatus('0','0','0','0','0')

    def __init__(self, parent, config, log_dir=None, offline=False):
        QtGui.QWidget.__init__(self)
        self.parent = parent
        self.config = config

        self.hbox = QtGui.QHBoxLayout()
        self.grid = QtGui.QGridLayout()
        self.setLayout(self.hbox)
        self.hbox.addLayout(self.grid)

        self.spinning_history = History(config.limits[-1], log_dir)

        self.build_ui()
        self.command_queue = Queue.Queue()
        self.MASThread = MASTCPThread(self, self.command_queue, offline)
        self.MASThread.start()
        self.connect(self.MASThread, QtCore.SIGNAL('got_status(PyQt_PyObject)'), self.got_status)
        self.connect(self.MASThread, QtCore.SIGNAL('reconnect(QString)'), self.reconnect_message)

    def got_status(self, status):
        """Handle a new status being received from the MAS controller

        This is called when a new status is obtained by the MASThread. The new
        measured spin rate is added to the plot and if the status has changed it
        is updated and displayed to the user in the controls GUI.

        Args:
            status: new status
        """
        self.spinning_history.add_point(status[1], int(status[0].spin))
        if self.status == status[0]:
            return

        self.status = status[0]
        self.update_displays()

    def update_displays(self):
        """Update GUI fields with status values."""
        self.spin_display.setText(self.status.spin.rjust(self.spin_digits))
        self.bearing_display.setText(self.status.bearing.rjust(self.pressure_digits))
        self.drive_display.setText(self.status.drive.rjust(self.pressure_digits))
        self.sense_display.setText(self.status.sense.rjust(self.pressure_digits))
        self.spin_set_display.setText(self.status.spin_set.rjust(self.spin_digits))

    def build_ui(self):
        """Set up the GUI elements."""
        self.build_control_grid()
        self.plot = HistoryPlot(self.spinning_history, self.config)
        self.hbox.addWidget(self.plot)

    def build_control_grid(self):
        """Set up the control elements."""
        self.grid = QtGui.QGridLayout()
        self.hbox.addLayout(self.grid)

        self.spin_display = QtGui.QLabel('    0', self)
        spin_font = QtGui.QFont(self.spin_display.font())
        spin_font.setPointSize(30)
        self.spin_display.setFont(spin_font)
        self.grid.addWidget(self.spin_display, 0, 0)

        self.build_mode_controls(1)
        self.auto_spin_controls(2)
        self.pressure_controls(3)
        spacer = QtGui.QSpacerItem(20, 40, QtGui.QSizePolicy.Minimum, QtGui.QSizePolicy.Expanding)
        self.grid.addItem(spacer, 7, 0, 1, 3)

    def build_mode_controls(self, row):
        """Set up the buttons for switching between manual and auto.

        Args:
            row: the row in the grid to place the buttons
        """
        self.manual_button = QtGui.QPushButton('Manual', self)
        self.manual_button.setCheckable(True)

        self.auto_button = QtGui.QPushButton('Auto', self)
        self.auto_button.setCheckable(True)

        mode_hbox = QtGui.QHBoxLayout()
        mode_hbox.addWidget(self.manual_button)
        mode_hbox.addWidget(self.auto_button)
        self.grid.addLayout(mode_hbox, row, 0, 1, 3)

        self.mode_controls = QtGui.QButtonGroup()
        self.mode_controls.addButton(self.manual_button)
        self.mode_controls.addButton(self.auto_button)
        self.mode_controls.buttonClicked.connect(self.mode_clicked)

    def pressure_controls(self, row):
        """Set up the GUI elements for monitoring/controlling pressures.

        Args:
            row: the row in the grid to start placing the GUI elements
        """
        self.grid.addWidget(QtGui.QLabel('Drive (mBar):', self), row, 0)
        self.drive_display = QtGui.QLabel('   0', self)
        self.drive_control = PressureControl(self)
        self.grid.addWidget(self.drive_display, row, 1)
        self.grid.addWidget(self.drive_control, row, 2)
        self.connect(self.drive_control, QtCore.SIGNAL('set_pressure(int)'), self.set_drive)

        self.grid.addWidget(QtGui.QLabel('Bearing (mBar):', self), row+1, 0)
        self.bearing_display = QtGui.QLabel('   0', self)
        self.bearing_control = PressureControl(self)
        self.grid.addWidget(self.bearing_display, row+1, 1)
        self.grid.addWidget(self.bearing_control, row+1, 2)
        self.connect(self.bearing_control, QtCore.SIGNAL('set_pressure(int)'), self.set_bearing)

        self.grid.addWidget(QtGui.QLabel('Bearing Sense (mBar):', self), row+2, 0)
        self.sense_display = QtGui.QLabel('   0', self)
        self.grid.addWidget(self.sense_display, row+2, 1)

        self.drive_control.setEnabled(False)
        self.bearing_control.setEnabled(False)

    def auto_spin_controls(self, row):
        """Set up the GUI elements for auto mode spin control.

        Args:
            row: the row in the grid to place the GUI elements
        """
        self.spin_set_button = QtGui.QPushButton('Set Spin Rate', self)
        self.spin_set_display = QtGui.QLabel('    0', self)
        self.spin_set_box = QtGui.QSpinBox(self)
        self.spin_set_box.setRange(0, 70000)

        self.grid.addWidget(self.spin_set_button, row, 0)
        self.grid.addWidget(self.spin_set_display, row, 1)
        self.grid.addWidget(self.spin_set_box, row, 2)

        self.spin_set_button.clicked.connect(self.set_spin)

        self.spin_set_button.setEnabled(False)
        self.spin_set_box.setEnabled(False)

    def mode_clicked(self, button):
        """Handle a click on the mode control buttons.

        Sends the new mode command to the MAS controller and activates/
        deactivates the relevant GUI elements for each mode.

        Args:
            button: the button that was clicked
        """
        if button == self.manual_button:
            self.command_queue.put(('GM',))
            self.spin_set_button.setEnabled(False)
            self.spin_set_box.setEnabled(False)
            self.drive_control.setEnabled(True)
            self.bearing_control.setEnabled(True)
            self.drive_control.setValue(int(self.status.drive))
            self.bearing_control.setValue(int(self.status.bearing))

        elif button == self.auto_button:
            self.command_queue.put(('GA',))
            self.spin_set_button.setEnabled(True)
            self.spin_set_box.setEnabled(True)
            self.drive_control.setEnabled(False)
            self.bearing_control.setEnabled(False)
            self.spin_set_box.setValue(int(self.status.spin))

    def set_bearing(self, val):
        """Send a bearing pressure to the MAS controller."""
        self.command_queue.put(('BP', (str(val),)))

    def set_drive(self, val):
        """Send a drive pressure to the MAS controller."""
        self.command_queue.put(('DP', (str(val),)))

    def set_spin(self):
        """Send the spin set point to the MAS controller."""
        spin_set = self.spin_set_box.value()
        self.command_queue.put(('DS', (str(spin_set),)))

    def reconnect_message(self, message):
        """Launch a dialog when connection errors arise.

        Asks the user whether to try to connect to the MAS controller again or
        terminate the program.

        Args:
            message: text to display about the connection issue
        """
        self.spinning_history.add_point(datetime.now(), np.ma.masked)
        dialog = QtGui.QMessageBox(self)
        dialog.setText(message)
        dialog.setInformativeText('Do you want to attempt to reconnect or end the program?')
        dialog.setStandardButtons(QtGui.QMessageBox.Retry | QtGui.QMessageBox.Abort)
        response = dialog.exec_()

        if response == QtGui.QMessageBox.Abort:
            self.MASThread.running = False
            self.MASThread.retry_dialog_open = False
            self.parent.quit()
        else:
            # disable all controls so the user must choose a mode before doing anything, just like when first starting
            # the values in the spin boxes get put to the current values of the MAS controller when a mode is chosen
            # old values sticking around can be unsafe (accidentally lead to crashes) if the spinning state has changed
            self.spin_set_button.setEnabled(False)
            self.spin_set_box.setEnabled(False)
            self.drive_control.setEnabled(False)
            self.bearing_control.setEnabled(False)

            # a button group must not be exclusive to uncheck buttons, so set it non-exclusive before unchecking
            # make the group exclusive again after the buttons are unchecked
            self.mode_controls.setExclusive(False)
            self.manual_button.setChecked(False)
            self.auto_button.setChecked(False)
            self.mode_controls.setExclusive(True)

            self.MASThread.retry_dialog_open = False

    def cleanup(self):
        """Ensure the connection to the MAS controller finishes properly.

        Makes sure the connection is close properly when the application is
        closing. Tells the MASThread to stop running and waits for it to
        actually finish before exiting.
        
        Also writes any unlogged data from the spinning history if logging is
        active.
        """
        self.MASThread.running = False
        while self.MASThread.isRunning():
            pass
        if self.spinning_history.logging is True:
            self.spinning_history.end_logging()
        
class History():
    '''Updatable store of value of a parameter over time

    Holds a history of time-value pairs and allows adding additional points
    indefinitely by periodically discarding points older than history_length.

    Args:
        history_length: Optional timedelta setting how long to keep data
        log_dir: Optional directory name to store log files
    '''
    history_buffer = 1000 # number of empty points to allocate in the history (controls reallocation frequency)

    def __init__(self, history_length = timedelta(hours=24), log_dir=None):
        self.history_length = history_length

        if log_dir is None:
            self.log_dir = ''
            self.can_save = True
        else:
            self.log_dir = log_dir
            if os.path.isdir(self.log_dir) and os.access(self.log_dir, os.W_OK):
                self.can_save = True
            else: # disallow saving if the log_dir does not exist or can't be written to
                self.can_save = False

        self.times, self.values = self.allocate_arrays(self.history_buffer*2)
        self.filled_points = 0

        self.logging = False
        self.log_start = datetime.now()
        self.log_end = self.log_start
    
    def allocate_arrays(self, points):
        '''
        Allocate and return new empty time and value arrays

        Args:
            points: integer number of points to allocate
        Returns:
            times: array of times
            values: array of value
        '''
        # Store values as a masked array to make it possible to break the line during disconnections
        return np.zeros((points,), dtype=np.object), np.ma.masked_array(np.zeros((points,), dtype=np.int))

    def arrays_full(self):
        '''Handle a new point being added when the arrays are full

        New time and value arrays are created and the latter part of the old
        ones are copied into them. If history_buffer points can be discarded from
        the beginning of the old arrays while keeping points with at least
        history_length in time then the new arrays will be the same size as the
        old ones and those early points will be discarded. Otherwise all of the
        points are kept and the arrays grow by history_buffer.

        This keeps at least history_length worth of points without using too
        much memory no matter how long the program runs for. filled_points is
        updated to reflect the current valid points in the new arrays.

        If logging is active then the log file will updated.
        '''
        if self.logging is True:
            self.write_log()

        old_points = len(self.times)
        if self.times[-1]-self.times[self.history_buffer] >= self.history_length:
            new_points = old_points
            keep = old_points-self.history_buffer
        else:
            new_points = old_points+self.history_buffer
            keep = old_points

        times, values = self.allocate_arrays(new_points)
        times[:keep] = self.times[-keep:]
        values[:keep] = self.values[-keep:]
        self.filled_points = keep
        self.times = times
        self.values = values

    def add_point(self, time, value):
        '''Add a data point to the history.

        Args:
            time: datetime object of the data point
            value: parameter value
        '''
        if self.filled_points < len(self.times):
            self.times[self.filled_points] = time
            self.values[self.filled_points] = value
            self.filled_points += 1
        else: # if the arrays are full reallocate before adding the point
            self.arrays_full()
            self.add_point(time, value)
    
    def active_range(self, time_range):
        '''Return the active portion of history going back at most time_range

        The start time of the active range is the last filled point minus the
        time_range. The portion of the time and value arrays from this start
        point to the last filled point are returned.

        Args:
            time_range: timedelta specifying time range to include
        Returns:
            times: active time points
            values: active values
        '''
        if self.filled_points <= 1:
            return None, None

        start_time = self.times[self.filled_points-1]-time_range
        start_point = self.times[:self.filled_points].searchsorted(start_time)
        active_slice = slice(start_point, self.filled_points)
        return self.times[active_slice], self.values[active_slice]
    
    def save_history(self):
        '''Save all filled points in the current arrays to a file'''
        if self.filled_points <= 1:
            return
        if self.can_save is False:
            raise RuntimeError('Cannot save data')
        
        start_time = self.times[0]
        end_time = self.times[self.filled_points-1]
        file_spec = self.save_name(start_time, end_time)
        with open(file_spec, 'w+') as file_:
            self.write_points(file_, slice(0, self.filled_points))
    
    def save_name(self, start_time, end_time):
        '''Generate a file name for a range of times
        
        Args:
            start_time: datetime of first point to save
            end_time: datetime of last point to save
        Returns:
            file: file string to save to
        '''
        name = '{}__{}_spin_log.dat'.format(self.string_time(start_time), self.string_time(end_time))
        return os.path.join(self.log_dir, name)

    def write_points(self, file_, write_slice):
        '''Write a slice of history to an open file handle

        Args:
            file_: An already opened file to write to
            write_slice: Slice object specifying which points to write
        '''
        times = self.times[write_slice]
        values = self.values[write_slice]
        for t, v in zip(times, values):
            line = '{} {:>6}\n'.format(self.string_time(t), str(v))
            file_.write(line)
    
    def string_time(self, time):
        '''Return a string formatted time suitable for saving and file names
        
        Args:
            time: datetime object to convert
        Returns:
            str_time: string representing the time
        '''
        return time.strftime('%Y-%m-%d-%H-%M-%S')

    def begin_logging(self):
        '''Start periodically saving data to a log file as it is collected'''
        if self.logging is True:
            raise RuntimeError('Cannot begin logging if logging is already active')

        self.log_start = datetime.now()
        self.log_end = self.log_start
        self.logging = True

    def write_log(self):
        '''Write data collected since the last write to the log file'''
        if self.logging is False:
            raise RuntimeError('Cannot write log data if logging is not active')
        if self.can_save is False:
            raise RuntimeError('Cannot save data')

        old_file = self.save_name(self.log_start, self.log_end)
        new_end = self.times[self.filled_points-1]
        new_file = self.save_name(self.log_start, new_end)

        start_point = self.times[:self.filled_points].searchsorted(self.log_end, 'right')
        log_slice = slice(start_point, self.filled_points)
        with open(old_file, 'a+') as file_:
            self.write_points(file_, log_slice)
        os.rename(old_file, new_file)

        self.log_end = new_end

    def end_logging(self):
        '''Write unlogged data and stop periodically writing data'''
        if self.logging is False:
            raise RuntimeError('Cannot end logging if logging is not active')

        self.write_log()
        self.logging = False
        self.log_start = datetime.now()
        self.log_end = self.log_start

class HistoryPlot(QtGui.QWidget, matplotlib.animation.TimedAnimation):
    '''Real time plot of the history of a parameter.

    Plots the history of the value of a parameter as a function of time and
    updates in real time.

    Args:
        history: A History object to plot
        config: a Configuration object to set the time limits and tick intervals
    '''
    def __init__(self, history, config):
        self.config = config
        self.fig = matplotlib.figure.Figure()

        QtGui.QWidget.__init__(self)
        FigureCanvasQTAgg(self.fig)
        matplotlib.animation.TimedAnimation.__init__(self, self.fig, interval = 1000)

        self.history = history
        self.initialize_plot()
        self.setup_widgets()

    def setup_widgets(self):
        '''Set up the GUI elements for the plot/controls.'''
        vbox = QtGui.QVBoxLayout()
        self.setLayout(vbox)
        vbox.addWidget(self.fig.canvas)
        slider = PlotRangeControl(self.config.limits, QtCore.Qt.Horizontal)
        vbox.addWidget(slider)
        vbox.addWidget(HistorySaveControls(self.history))

        self.connect(slider, QtCore.SIGNAL('new_max_range(PyQt_PyObject)'), self.set_max_range)

    def initialize_plot(self):
        '''Setup the plot in an intial default state.'''
        self.line1 = matplotlib.lines.Line2D([], [], color='blue')
        self.axes = self.fig.add_subplot(111)
        self.axes.add_line(self.line1)
        self.axes.autoscale(True, 'x', True)
        self.axes.xaxis.set_major_locator( matplotlib.dates.SecondLocator())
        self.axes.xaxis.set_major_formatter(matplotlib.dates.DateFormatter('%H:%M:%S'))
        self.max_range = timedelta(hours=24)
        self.axes.grid(True)

    def set_max_range(self, value):
        '''Set the maximum time range displayed by the plot.

        If the selected plot range exceeds history_length then the maximum plot
        range will be set to history_length.

        Args:
            value: timedelta object holding the new maximum plot range
        '''
        if value > self.history.history_length:
            self.max_range = self.history.history_length
        else:
            self.max_range = value

    def select_locator(self, time_range):
        '''Setup a tick locator and formatter based on the range of the plot.

        In order to keep the tick labels from overlapping the time interval of
        the ticks must depend on the range of times in the plot.

        Args:
            time_range: timedelta object with the difference between the xlims
        '''
        for i, limit in enumerate(self.config.limits):
            if time_range < limit:
                loc = self.config.locators[i]
                interval = self.config.intervals[i]
                break
        else:
            loc = matplotlib.dates.HourLocator
            interval = 6

        if loc == matplotlib.dates.SecondLocator:
            specifier = range(0, 60, interval)
            format = '%H:%M:%S'
        elif loc == matplotlib.dates.MinuteLocator:
            specifier = range(0, 60, interval)
            format = '%H:%M'
        elif loc == matplotlib.dates.HourLocator:
            specifier = range(0, 24, interval)
            format = '%H:%M'

        self.axes.xaxis.set_major_locator(loc(specifier))
        self.axes.xaxis.set_major_formatter(matplotlib.dates.DateFormatter(format))

    def new_frame_seq(self):
        '''Provide an infinite counter to generate infinite frames.

        TimedAnimation requires an iterator to generate frames. An infinite
        counter causes it to draw frames until the program is closed.
        '''
        return itertools.count(0, 1)

    def _draw_frame(self, framedata):
        '''Draw new up to date plot.'''
        times, values = self.history.active_range(self.max_range)
        if times is None and values is None:
            return

        self.line1.set_data(times, values)
        self._drawn_artists = [self.line1]

        # force autoscaling of the x axis
        self.axes.autoscale(True, 'x', True)
        self.axes.relim()
        self.axes.autoscale_view()

        # set y limits
        value_min = np.amin(values)
        value_max = np.amax(values)
        self.axes.set_ylim(value_min-100, value_max+100)

        # format the x axis labels
        left, right = self.axes.get_xlim()
        time_range = timedelta(days=right-left)
        self.select_locator(time_range)

class PlotRangeControl(QtGui.QWidget):
    '''Slider for setting/displaying the maximum plot range

    The slider selects from the time ranges in time_limits. The currently
    selected range is also displayed in the GUI.

    Args:
        time_limits: list of possible timedelta plot ranges
        *args: args to pass to QSlider
    '''
    def __init__(self, time_limits, *args):
        QtGui.QWidget.__init__(self)

        self.time_limits = time_limits

        self.slider = QtGui.QSlider(*args)
        hbox = QtGui.QHBoxLayout()
        self.setLayout(hbox)
        hbox.addWidget(QtGui.QLabel('Maximum Plot Range:'))
        self.range_display = QtGui.QLabel(self.custom_time_str(self.time_limits[-1]))
        hbox.addWidget(self.range_display)
        hbox.addWidget(self.slider)

        self.slider.setPageStep(1)
        max_slider = len(self.time_limits)-1
        self.slider.setRange(0, max_slider)
        self.slider.setValue(max_slider)
        self.slider.valueChanged.connect(self.new_position)

    def new_position(self, value):
        '''Handle a new slider position.

        Displays the new time range and emits new_max_range signal with the new
        range.

        Args:
            value: the new slider value
        '''
        range = self.time_limits[value]
        self.range_display.setText(self.custom_time_str(range))
        self.emit(QtCore.SIGNAL('new_max_range(PyQt_PyObject)'), range)

    def custom_time_str(self, timedelta_):
        '''Create constant space string representation of a timedelta.

        Args:
            timedelta_: timedelta to convert to a string
        '''
        sec = int(timedelta_.total_seconds())
        mm, ss = divmod(sec, 60)
        hh, mm = divmod(mm, 60)
        if hh > 99:
            raise ValueError('Cannot properly represent times with triple digit hours')
        return '%02d:%02d:%02d' % (hh, mm, ss)

class HistorySaveControls(QtGui.QWidget):
    '''Buttons to control saving and logging history
    
    Args:
        history: History object to save data from
    '''
    def __init__(self, history):
        QtGui.QWidget.__init__(self)
        self.history = history

        self.save_button = QtGui.QPushButton('Save Spinning History', self)
        self.save_button.clicked.connect(self.history.save_history)

        hbox = QtGui.QHBoxLayout()
        self.setLayout(hbox)
        hbox.addWidget(self.save_button)

        self.log_button = QtGui.QPushButton('Begin Logging', self)
        self.log_button.clicked.connect(self.switch_logging)
        hbox.addWidget(self.log_button)

        # Disable buttons if data cannot be saved
        if self.history.can_save is False:
            self.save_button.setEnabled(False)
            self.log_button.setEnabled(False)
    
    def switch_logging(self):
        '''Switch logging on or off based on its current state'''
        if self.history.logging is True:
            self.history.end_logging()
            self.log_button.setText('Begin Logging')
        elif self.history.logging is False:
            self.history.begin_logging()
            self.log_button.setText('End Logging')

class PressureControl(QtGui.QSpinBox):
    """SpinBox for contolling a pressure value.

    PressureControl is a QSpinBox that rounds values to the nearest 10, in order
    to match the behaviour of a MAS controller. Steps go by 10 and upon
    editingFinished the value gets rounded to 10. A set_pressure signal is
    emitted after stepping with the arrows, pressing enter, or clicking away.

    Args:
        parent: parent widget
    """
    def __init__(self, parent):
        QtGui.QSpinBox.__init__(self, parent)
        self.setRange(0, 5000)
        self.setSingleStep(10)
        self.editingFinished.connect(self.new_value)

    def stepBy(self, step):
        """Overload stepping to handle non-multiples of 10.

        Ensures that if the current value is not a multiple of 10 the value
        steps up or down to the next multiple of 10 instead of stepping by
        10. Emits set_pressure signal after stepping.

        Args:
            step: number of steps to take
        """
        if step > 0:
            self.setValue(self.value()//10*10)
        elif step < 0:
            val = self.value()
            if val%10 != 0:
                self.setValue((self.value()//10+1)*10)
        QtGui.QSpinBox.stepBy(self, step)
        self.emit(QtCore.SIGNAL('set_pressure(int)'), self.value())

    def new_value(self):
        """Round value to nearest multiple of 10.

        Rounds the box value to the nearest multiple of ten to match the
        behaviour of the MAS controller. Emits set_pressure signal after rounding.
        """
        if self.value()%10 != 0:
            self.setValue(int(round(self.value(), -1)))
        self.emit(QtCore.SIGNAL('set_pressure(int)'), self.value())

class MASTCPThread(QtCore.QThread):
    """Operates a thread for asynchronous communication with a MAS controller.

    Add commands to the queue in the form of (command,(args...)) to send them.
    Periodically polls MAS controller for its status and emits got_status signal
    with the returned values. Emits reconnect signal in response to communication
    socket errors or timeouts. Set retry_dialog_open to False after reconnect
    signal to break loop and try to reconnect. Set running to False to break
    execution loop and finish thread.

    Args:
        parent: parent widget
        queue: command queue
    """
    def __init__(self, parent, queue, offline):
        QtCore.QThread.__init__(self, parent)
        self.parent = parent
        self.queue = queue
        self.offline = offline
        self.running = True
        self.retry_dialog_open = False

    def run(self):
        """Run loop to connect to MAS controller.

        Runs a loop so long as running is True that connects to MAS controller
        and handles commands and polling. Emits reconnect signal in response to
        socket errors or timeouts and waits for retry_dialog_open to be false to
        continue loop.
        """
        while self.running:
            if self.offline:
                self.run_offline()
                continue

            try:
                self.run_connection()
            except socket.timeout: # socket.timeout has to be before socket.error because it is a subtype of it
                self.retry_dialog_open = True
                self.emit(QtCore.SIGNAL('reconnect(QString)'),
                    'Timeout error: Check that the MAS controller is in remote mode.')
            except socket.error:
                self.retry_dialog_open = True
                self.emit(QtCore.SIGNAL('reconnect(QString)'),
                    'Connection error: Check that no other programs are connected to the MAS controller.\nYou may need to enter "set mas off" in RNMRA')
            while self.retry_dialog_open:
                self.msleep(100)

    def run_connection(self):
        """Connect to the MAS controller and loop to poll status/send commands.

        Opens a TCP socket to the MAS controller and loops as long as running is
        True. On each loop either send the next command in the queue or poll the
        status if the queue is empty. The loop is broken if running is set to
        False or by socket errors. Signal got_status is emitted every time the
        status is polled. In the event of a timeout an attempt is made to retry
        communication before breaking the loop.
        """
        with MASTCPHandler() as handler:
            while self.running:
                try:
                    if self.queue.empty():
                        status, status_time = self.poll_status(handler)
                        self.emit(QtCore.SIGNAL('got_status(PyQt_PyObject)'), (status, status_time))
                        self.sleep(1)
                    else:
                        command = self.queue.get()
                        handler.send_command(*command)
                        self.msleep(50)
                except socket.timeout as error:
                    if self.retry_connection(handler) is False:
                        raise error

    def poll_status(self, handler):
        """Poll the status of the MAS controller.

        Request and return the status of the controller and a corresponding
        timestamp.

        Args:
            handler: MASTCPHandler for the MAS controller
        Returns:
            Tuple containing a MASStatus and its timestamp
        """
        response = handler.send_command('AS', tuple())
        self.msleep(50)
        status_time = datetime.now()
        spin_set = handler.send_command('VD', tuple())
        response.append(spin_set[0])
        status = MASStatus(*response[1:])
        return (status, status_time)

    def retry_connection(self, handler):
        """Test whether the connection to the MAS controller is still working.

        Test the connection several times over a few seconds to see if a timeout
        issue was temporary or a fluke. Return True if a connection test is
        successsful or False if the time limit is reached.

        Args:
            handler: handler: MASTCPHandler for the MAS controller
        Returns:
            Boolean indicating success or failure of the retry attempts
        """
        start_time = time.time()
        while time.time()-start_time < 4*handler.timeout_limit:
            if handler.test_connection():
                self.sleep(handler.timeout_limit)
                handler.socket.recv(128)
                return True
            self.msleep(100)
        return False

    def run_offline(self):
        """Generate fake spinning data for running without a MAS controller.

        This function generates a very simple (and unrealistic) pattern of
        spinning data that repeatedly ramps from 0 to 100. This fake data
        enables development and testing of this program without needing to
        actually connect to a MAS controller. Commands in the queue are printed
        to the terminal for debugging purposes.
        """
        for n in range(100):
            if self.running is False:
                return

            if self.queue.empty():
                status = MASStatus(str(n),'0','0','0','0')
                status_time = datetime.now()
                self.emit(QtCore.SIGNAL('got_status(PyQt_PyObject)'), (status, status_time))
            else:
                command = self.queue.get()
                print(command)
            self.msleep(50)
        self.parent.spinning_history.add_point(datetime.now(), np.ma.masked)

class MASTCPHandler:
    """Manages TCP communication with an MAS controller.

    Implements context management api for use in with blocks. Connects to MAS
    controller via TCP socket to send commands and receive responses. Call
    send_command to send a command to the MAS controller and receive the reponse
    as a return value.

    Args:
        address: optional tuple of (node, port) to connect to the MAS controller
            if not specified then the values will be loaded from symbols.sh
    """
    timeout_limit = 3

    def __init__(self, address=None):
        if address == None:
            address = self.get_address()

        self.command_table = self.load_cfg()

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(self.timeout_limit)
        self.socket.connect(address)

    def __exit__(self, type, value, traceback):
        """Close socket on exit."""
        time.sleep(0.1)
        self.socket.close()

    def __enter__(self):
        """Return self when used in with block."""
        return self

    def find_cfg(self):
        """Find MAS command configuration file.

        Returns:
            Configuration file path
        """
        if 'RNMR_COMMON' in os.environ:
            search_path = os.path.join(os.environ['RNMR_COMMON'], 'rnmra/cfgmas.dat')
        else:
            search_path = ''

        if os.path.isfile('cfgmas.dat'):
            return 'cfgmas.dat'
        elif os.path.isfile(search_path):
            return search_path
        else:
            raise IOError('Cannot find configuration file')

    def load_cfg(self):
        """Load MAS command configuration file.

        Returns:
            dict of MAS commands
        """
        cfg_file = self.find_cfg()
        with open(cfg_file) as file_:
            lines = file_.readlines()

        if lines[0].split()[0] != 'MASCMD':
            raise IOError('Invalid configuration file')

        commands = {}
        for line in lines[1:]:
            line = line.strip()
            if line == '':
                break

            if line == ';':
                continue

            entry = line.split()
            vals = entry[1].split(',')
            if entry[0] != vals[0]:
                raise IOError('Invalid configuration entry')

            commands[vals[0]] = [int(vals[1]), int(vals[2])]
        return commands

    def find_symbols(self):
        """Find spectrometer symbols shell script file.

        Returns:
            Configuration file path
        """
        if 'RNMR_SPECIFIC' in os.environ:
            search_path = os.path.join(os.environ['RNMR_SPECIFIC'], 'symbols.sh')
        else:
            search_path = ''

        if os.path.isfile('symbols.sh'):
            return 'symbols.sh'
        elif os.path.isfile(search_path):
            return search_path
        else:
            raise IOError('Cannot find symbols file')

    def get_address(self):
        """Load MAS controller TCP connection information.

        Returns:
            tuple(TCP_node, TCP_port) for use in connecting to MAS controller
        """
        symbols_file = self.find_symbols()

        with open(symbols_file) as file_:
            lines = file_.readlines()

        node_line = ['export', 'TRM1_TCP_NODE', '=']
        port_line = ['export', 'TRM1_TCP_PORT', '=']
        for line in lines:
            parsed = list(shlex.shlex(line))
            if parsed[:3] == node_line and len(parsed) == 4:
                node = parsed[3][1:-1]
            elif parsed[:3] == port_line and len(parsed) == 4:
                port = int(parsed[3][1:-1])

        if 'port' not in locals() or 'node' not in locals():
            raise IOError('MAS controller address and port not in symbols.sh')

        return (node, port)

    def send_command(self, command, args=tuple()):
        """Send a command to the MAS controller and receive a response.

        The command and its arguments are sent to the MAS controller and a
        response is returned. The command and the number of args must match the
        command table. If the response takes to long socket.timeout is raised.

        Args:
            command: two letter string indicating the command
            args (optional): collection of arguments to send with the command

        Returns:
            Response as list of strings
        """
        if command not in self.command_table:
            raise ValueError('Unknown command')
        else:
            nargs, _ = self.command_table[command]

        if len(args) != nargs:
            raise ValueError('Incorrect number of arguments for command')
        nargs = len(args)
        if nargs >= 1:
            command+=' '+' '.join(args)

        encoded = self.encode_message(command)
        self.socket.send(encoded)

        start_time = time.time()
        received = ''
        while time.time()-start_time < self.timeout_limit:
            received += self.socket.recv(80)
            if received[-2:] == '\x8d\x8a': # \r\n terminator
                break
        else:
            raise socket.timeout('MAS controller took too long to respond')

        response = self.decode_message(received)

        return response

    def encode_message(self, message):
        """Create byte string to send to MAS controller.

        The commands are sent as ASCII but with the first bit high and the end
        is marked with \r.

        Args:
            message: command string to encode

        Returns:
            Encoded byte string
        """
        encoded_bytes = [chr(ord(n)+128) for n in message]
        encoded_bytes.append('\x8d') # \r terminator
        return ''.join(encoded_bytes)

    def decode_message(self, message):
        """Decode response from MAS controller.

        The responses are sent as ASCII but with the first bit high and the end
        is marked with \r\n. Multiple reponse values are separated by a space in
        the response and will be separated into a list of values.

        Args:
            message: response byte string to decode

        Returns:
            Response as list of strings
        """
        decoded_bytes = [chr(ord(n)-128) for n in message]
        return ''.join(decoded_bytes)[:-2].split()

    def test_connection(self):
        """Test the connection to the MAS controller.

        Send the MA command and return a boolean based on whether the expected
        respnse of OK is received.

        Returns:
            Boolean indicating success or failure of the test
        """
        try:
            message = self.send_command('MA')
        except socket.error:
            message = ''
        
        return message == ['OK']

class Configuration():
    '''Configures the available ranges and tick intervals for history plotting'''
    def __init__(self):
        config_dir = os.path.dirname(os.path.realpath(__file__))
        config_file = os.path.join(config_dir, 'config_times.dat')
        with open(config_file) as file_:
            raw = file_.readlines()
        
        if raw[0] != 'Limit Ticks\n':
            raise IOError('Invalid Configuration')
        
        self.limits = []
        self.locators = []
        self.intervals = []

        for entry in raw[1:]:
            self.add_entry(entry)

    def add_entry(self, entry):
        tokens = entry.split()
        if len(tokens) != 4:
            raise IOError('Invalid Configuration')

        limit_value = int(tokens[0])
        if tokens[1] == 's':
            limit = timedelta(seconds=limit_value)
        elif tokens[1] == 'm':
            limit = timedelta(minutes=limit_value)
        elif tokens [1] == 'h':
            limit = timedelta(hours=limit_value)
        else:
            raise IOError('Invalid time unit in configuration: {}'.format(tokens[1]))
        
        interval = int(tokens[2])
        if tokens[3] == 's':
            interval_delta = timedelta(seconds=interval)
            locator = matplotlib.dates.SecondLocator
        elif tokens[3] == 'm':
            interval_delta = timedelta(minutes=interval)
            locator = matplotlib.dates.MinuteLocator
        elif tokens [3] == 'h':
            interval_delta = timedelta(hours=interval)
            locator = matplotlib.dates.HourLocator
        else:
            raise IOError('Invalid time unit in configuration: {}'.format(tokens[3]))
        
        if interval_delta >= limit:
            raise IOError('Tick interval must be less than time limit')
        if len(self.limits) >= 1 and limit <= self.limits[-1]:
            raise IOError('Time limits must be in increasing order')
        
        self.limits.append(limit)
        self.locators.append(locator)
        self.intervals.append(interval)

def view_gui():
    parser = argparse.ArgumentParser()
    parser.add_argument('-l','--log_dir', help='Set the directory to save spinning logs to', type=str)
    parser.add_argument('-o','--offline', help='Run offline using simple fake spinning values', action='store_true')
    args = parser.parse_args()

    config = Configuration()

    app = QtGui.QApplication(sys.argv)
    wind = MASView(app, config, args.log_dir, args.offline)
    app.aboutToQuit.connect(wind.cleanup)
    wind.show()
    sys.exit(app.exec_())

def send_signals():
    with MASTCPHandler() as a:
        while True:
            command_string = raw_input('Enter command: ')
            if command_string == 'exit':
                break
            elif command_string == 'test':
                print(a.test_connection())
                continue

            split_command = command_string.split()
            command = split_command[0]
            args = tuple(split_command[1:])
            if command not in a.command_table:
                print('Invalid command')
                continue

            print(a.send_command(command, args))

if __name__=='__main__':
    view_gui()
    # send_signals()
