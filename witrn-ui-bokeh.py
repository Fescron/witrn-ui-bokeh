## Development started on 2023-11-25 (Fescron) ##

# Run with:          bokeh serve --show witrn-ui-bokeh.py
# Get more log-info: bokeh serve --log-level trace --show witrn-ui-bokeh.py

# Starting-sources
#  - https://coderzcolumn.com/tutorials/data-science/bokeh-work-with-realtime-streaming-data
#  - https://docs.bokeh.org/en/latest/docs/user_guide/server/app.html#updating-from-threads


# TODO Take an average of readings between log/plot periods instead of "throwing the values away"?
# TODO Don't plot/log power (and current) to increase performance? -> Defines?

# TODO Add functionality to read csv-files and plot them again
# TODO Add RadioButtonGroup to select the "real-time" or "after the fact" plotting-mode



from bokeh.models import ColumnDataSource, LinearAxis, DataRange1d, DatetimeTickFormatter, Legend, PrintfTickFormatter, Button, TextInput, Div, Select, Label, NumericInput, Switch
from bokeh.io import curdoc
from bokeh.plotting import figure
from bokeh.palettes import Category20c_20 as COLOR # See https://docs.bokeh.org/en/latest/docs/reference/palettes.html
from bokeh.layouts import column, row

from datetime import datetime, timedelta
from threading import Thread
from functools import partial
import csv
import time

from driver import USBMeter
from driver.protocol import KnownDevice, HIDPacket, Command
from enum import Enum


# Basic plot settings
plot_title = "Witrn" # Spaces get replaced with "-" for the filename
PLOT_WIDTH  = 1200
PLOT_HEIGHT = 600

# Default Y-axis-ranges
CURR_START = 0
CURR_END   = 4
VOLT_START = 0
VOLT_END   = 25
POW_START  = 0
POW_END    = 100

# Maximum amount of X-axis data-points before discarting old data
MAX_X_POINTS = 6000


# Save curdoc() to make sure all threads see the same document
doc = curdoc()

# Create the data-source
data_source = ColumnDataSource(data = {"DateTime": [],
                                       "DateTimeStr": [],
                                       "voltage": [],
                                       "current": [],
                                       "power": []})

# Tooltip (hover) settings
TOOLTIPS = f"""
    <div>
        <font color="{COLOR[4]}">Voltage: @voltage V</font><br>
        <font color="{COLOR[0]}">Current: @current A</font><br>
        <font color="{COLOR[8]}">Power: @power W</font><br>
        Time: @DateTimeStr<br>
    </div>
"""

# Tool settings
TOOLS = "pan,box_zoom,xwheel_zoom,ywheel_zoom,xwheel_pan,reset,undo,redo,save,hover"

# Basic figure settings
fig = figure(width=PLOT_WIDTH, height=PLOT_HEIGHT,
            #  title=plot_title,
             y_range=(VOLT_START, VOLT_END),
             x_axis_type="datetime",
             active_scroll ="xwheel_zoom",
            #  sizing_mode="stretch_width", height=450,
            #  toolbar_location="above",
            #  output_backend="webgl",
             tooltips=TOOLTIPS, tools=TOOLS)

# X-axis settings
fig.xaxis.axis_label = "Time"
fig.xaxis.axis_label_text_font_style = "bold"
fig.xaxis[0].formatter = DatetimeTickFormatter(milliseconds="%H:%M:%S.%3N", seconds="%H:%M:%S",
                                               minsec="%H:%M:%S", minutes="%H:%M", hourmin="%H:%M") # Reformat datetime zoom-levels

# Y-axis settings
fig.yaxis.axis_label = "Voltage"
fig.yaxis.axis_label_text_color = COLOR[4]
fig.yaxis.axis_label_text_font_style = "bold"

# Add extra Y-axis
fig.extra_y_ranges["A"] = DataRange1d(start=CURR_START, end=CURR_END)
ax2 = LinearAxis(y_range_name="A", axis_label="\nCurrent", axis_label_text_color=COLOR[0], axis_label_text_font_style = "bold") # \n for extra whitespace
fig.add_layout(ax2, 'right')

# Add extra Y-axis
fig.extra_y_ranges["W"] = DataRange1d(start=POW_START, end=POW_END)
ax3 = LinearAxis(y_range_name="W", axis_label="Power", axis_label_text_color=COLOR[8], axis_label_text_font_style = "bold")
fig.add_layout(ax3, 'right')

# Axis tick formatting settings
fig.yaxis[0].formatter = PrintfTickFormatter(format="%s V")
fig.yaxis[1].formatter = PrintfTickFormatter(format="%s A")
fig.yaxis[2].formatter = PrintfTickFormatter(format="%s W")

# Fancy figure settings
fig.background_fill_color = "ivory"
fig.border_fill_color = "whitesmoke"
fig.toolbar.autohide = True
fig.add_layout(Legend(orientation="horizontal", spacing=20), "above")
fig.legend.click_policy="hide"
fig.title.text_font_size = "14pt"
fig.title.align = "center"

# Manually add the title in the "correct" place
new_title = Label(x=0, y=-50, x_units='screen', y_units='screen', text=plot_title, text_font_size="16pt", text_font_style="bold")
fig.add_layout(new_title, "above")

# Add the plot-lines
fig.line(x="DateTime", y="voltage", line_color=COLOR[4], line_width=2, source=data_source, legend_label="Voltage")
fig.line(x="DateTime", y="current", line_color=COLOR[0], line_width=2, source=data_source, legend_label="Current", y_range_name="A")
fig.line(x="DateTime", y="power",   line_color=COLOR[8], line_width=2, source=data_source, legend_label="Power",   y_range_name="W", visible=False)

# CSV logging variables/defaults
logging = False
log_period_ms = 250
csv_file = ""

# Meter variables/defaults
plot_period_ms = 0
meter = None
last_meas_time = None
last_plot_time = None
last_log_time  = None
invert_current = True
selected_device = "C4"

# Create a dropdown for the device-selection, buttons to open and close the connection, a status-label
# as well as a switch to change the current-sign-logic
device_select = Select(title="Device", value=selected_device, options=KnownDevice._member_names_)
open_conn_button  = Button(label="Open Connection",  button_type="success", align="center")
close_conn_button = Button(label="Close Connection", button_type="danger",  align="center", disabled=True)
status_label = Div(text="", width=305, align="center")
invert_sign_switch = Switch(active=invert_current, align="center")

# Create a title and log-period input-field, buttons to start and stop logging, an input-field
# for the plot-period as well as a button to reset the values in the ColumnDataTable
title_input = TextInput(title="Title", value=plot_title)
log_period_input = NumericInput(title="Log Period [ms]", value=log_period_ms)
start_log_button = Button(label="Start Logging", button_type="success", align="center")
stop_log_button  = Button(label="Stop Logging",  button_type="danger",  align="center", disabled=True)
plot_period_input = NumericInput(title="Plot Period [ms]", value=plot_period_ms)
clear_plot_button = Button(label="Clear Plot",    button_type="warning", align="center")

# Create a label to show measurement-values
meas_text = """
            <div>
                <br>
                <br>
                <br>
            </div>
        """
measurement_label = Div(text=meas_text, width=PLOT_WIDTH)

# State-machine variable
class state(Enum):
    STOPPED = 0
    INITIALIZING = 1
    RUNNING = 2
    STOPPING = 3
main_state = state.STOPPED


def on_packet(packet: HIDPacket):
    global main_state, last_meas_time, last_plot_time, last_log_time

    if main_state != state.RUNNING:
        return
    if packet.payload.command != Command.DAT_RECV:
        return

    datetime_now = datetime.now()
    datetime_string = datetime_now.strftime('%H:%M:%S.%f')[:-3] # TODO Do actual µs to ms rounding instead of [:-3] (?)

    if last_meas_time == None:
        last_meas_time = datetime_now
    if last_log_time == None:
        last_log_time = datetime_now
    if last_plot_time == None:
        last_plot_time = datetime_now

    data = packet.payload.data

    # TODO Use data.dp, data.dn, data.tempIn, data.tempOut?

    if invert_current:
        current = -data.current
    else:
        current = data.current

    power = data.voltage * current

    if logging and (datetime_now - last_log_time) > timedelta(milliseconds=log_period_ms):
        with open(csv_file, 'a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([datetime_now,
                            data.voltage,
                            current,
                            power,
                            data.ah, data.wh, data.recmA, data.recTime, data.recGrp+1, data.runTime])
        last_log_time = datetime_now

    if (datetime_now - last_plot_time) > timedelta(milliseconds=plot_period_ms):
        # Update the document from a callback
        doc.add_next_tick_callback(partial(update,
                                        datetime=datetime_now,
                                        datetimestr=datetime_string, 
                                        voltage=data.voltage,
                                        current=current,
                                        power=power))
        last_plot_time = datetime_now

    if (datetime_now - last_meas_time) > timedelta(milliseconds=log_period_ms):
        meas_text = f"""
            <div>
                &emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;
                <b>Last Measurements:&emsp;</b>
                <font color="{COLOR[4]}">Voltage: {f'{data.voltage:.3f}'} V</font> //
                <font color="{COLOR[0]}">Current: {f'{current:.3f}'} A</font> // 
                <font color="{COLOR[8]}">Power: {f'{power:.3f}'} W</font> //
                Time: {datetime_string}<br>
                &emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;
                <b>Accumulated Data: &emsp;&ensp;</b>
                Capacity: {f'{data.ah:.3f}'} Ah // Energy: {f'{data.wh:.3f}'} Wh // 
                Threshold: {data.recmA} mA // Recording: {f'{str(timedelta(seconds=data.recTime))}'} //
                Group: {data.recGrp+1} // Uptime: {f'{str(timedelta(seconds=data.runTime))}'}
                &emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;&emsp;
                <b>Offline Recording: &emsp;&emsp; </b>Recording: {data.offHour} h // Remaining (?): {data.offPer} measurements // Reserved (?): {data.reserved}
            </div>
        """
        doc.add_next_tick_callback(lambda: measurement_label.update(text=meas_text)) # "lambda:" for in-line callbacks
        last_meas_time = datetime_now


def on_error(error: Exception):
    global main_state

    print(f"Error: {error}")
    doc.add_next_tick_callback(lambda: status_label.update(text=str(error))) # "lambda:" for in-line callbacks
    main_state = state.STOPPED


async def update(datetime, datetimestr, voltage, current, power):
    data_source.stream({"DateTime": [datetime],
                        "DateTimeStr": [datetimestr],
                        "voltage": [voltage],
                        "current": [current],
                        "power": [power]}, rollover=MAX_X_POINTS)


def main_method():
    global meter, main_state

    while True:
        if main_state == state.STOPPED:
            time.sleep(0.1)

        elif main_state == state.INITIALIZING:
            try:
                meter = USBMeter(KnownDevice[selected_device])
                meter.recv_callback(on_packet)
                meter.error_callback(on_error)
                meter.connect()
            except Exception as e:
                error_string = type(e).__name__ + ": " + str(e)
                print(f"Failed to connect: {error_string}")

                doc.add_next_tick_callback(lambda: status_label.update(text=error_string)) # "lambda:" for in-line callbacks
                doc.add_next_tick_callback(lambda: device_select.update(disabled=False))
                doc.add_next_tick_callback(lambda: open_conn_button.update(disabled=False))
                doc.add_next_tick_callback(lambda: invert_sign_switch.update(disabled=False))
                # doc.add_next_tick_callback(lambda: plot_period_input.update(disabled=False)) # Can be updated on-the-fly

                main_state = state.STOPPED
                continue

            if status_label.text != "":
                doc.add_next_tick_callback(lambda: status_label.update(text="")) # "lambda:" for in-line callbacks
            doc.add_next_tick_callback(lambda: close_conn_button.update(disabled=False))

            meter.start_read()

            main_state = state.RUNNING

        elif main_state == state.RUNNING:
            time.sleep(0.001)

        elif main_state == state.STOPPING:
            meter.stop_read()
            time.sleep(0.1)
            meter.disconnect()

            doc.add_next_tick_callback(lambda: device_select.update(disabled=False)) # "lambda:" for in-line callbacks
            doc.add_next_tick_callback(lambda: open_conn_button.update(disabled=False))
            doc.add_next_tick_callback(lambda: invert_sign_switch.update(disabled=False))
            # doc.add_next_tick_callback(lambda: plot_period_input.update(disabled=False)) # Can be updated on-the-fly

            main_state = state.STOPPED


def on_device_select(attr, old, new):
    global selected_device

    selected_device = new


def on_open_conn_button():
    global main_state

    device_select.update(disabled=True)
    open_conn_button.update(disabled=True)
    invert_sign_switch.update(disabled=True)
    # plot_period_input.update(disabled=True) # Can be updated on-the-fly
    on_clear_plot_button()
    main_state = state.INITIALIZING


def on_close_conn_button():
    global main_state

    close_conn_button.update(disabled=True)
    main_state = state.STOPPING


def on_invert_sign_switch(attr, old, new):
    global invert_current

    invert_current = new


def on_title_input(attr, old, new):
    global plot_title

    plot_title = new
    # fig.title.text = plot_title
    doc.add_next_tick_callback(lambda: new_title.update(text=new))


def on_log_period_input(attr, old, new):
    global log_period_ms

    log_period_ms = new


def on_start_log_button():
    global csv_file, logging

    title_input.update(disabled=True)
    log_period_input.update(disabled=True)
    start_log_button.update(disabled=True)
    stop_log_button.update(disabled=False)
    clear_plot_button.update(disabled=True)
    csv_file = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{plot_title.replace(' ', '-')}.csv"
    on_clear_plot_button()
    logging = True


def on_stop_log_button():
    global logging

    title_input.update(disabled=False)
    log_period_input.update(disabled=False)
    start_log_button.update(disabled=False)
    stop_log_button.update(disabled=True)
    clear_plot_button.update(disabled=False)
    logging = False


def on_plot_period_input(attr, old, new):
    global plot_period_ms

    plot_period_ms = new


def on_clear_plot_button():
    data_source.data = {"DateTime": [],
                        "DateTimeStr": [],
                        "voltage": [],
                        "current": [],
                        "power": []}


# Configure callbacks
device_select.on_change("value", on_device_select)
open_conn_button.on_click(on_open_conn_button)
close_conn_button.on_click(on_close_conn_button)
invert_sign_switch.on_change("active", on_invert_sign_switch)
title_input.on_change("value", on_title_input)
log_period_input.on_change("value", on_log_period_input)
start_log_button.on_click(on_start_log_button)
stop_log_button.on_click(on_stop_log_button)
plot_period_input.on_change("value", on_plot_period_input)
clear_plot_button.on_click(on_clear_plot_button)

# Define the layout
layout = column(row(device_select, open_conn_button, close_conn_button, status_label,
                    Div(text="Invert Current Sign:", align="center"), invert_sign_switch, align="center"), Div(),
                row(title_input, log_period_input, start_log_button, stop_log_button, Div(width=40),
                    plot_period_input, clear_plot_button, align="center"), Div(),
                measurement_label, Div(), fig) # Empty Divs to get more whitespace
doc.add_root(layout)

# Start the application
thread = Thread(target=main_method, daemon=True) # daemon=True for the thead to be stopped using Ctrl+C
thread.start()
