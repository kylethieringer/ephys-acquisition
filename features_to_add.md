# features to add

1. load and run a stimulus protocol in continuous mode to do current injection steps or voltage steps all data between trials is saved but save the timing of each stimulus block start and stop so that way they can be separated out in post processing into individual trial files later. the daq and camera should be 
2. add a dropdown menu in the gui of all the protocols in the protocol folder to select a saved one instead of needing to open the builder.
3. change the units scaling for the stimulus contsructor if in voltage or current clamp mode.
4. change the ampcmd units to voltage instead of pA and scale factor of the amp command is 20mV/V
5. move channel y settings to acquisition tab
6. move start stop acquisition buttons to bottom bar with record and stop record.
7. show the membrane current display in picoamps instead of nanoamps x 0.001
8. save files first as a binary file and then after everything is shutdown, read binary file and then save a copy in hdf5 format
9. update readme and docs