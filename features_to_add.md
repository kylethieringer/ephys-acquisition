# features to add

... what is the buffer size for data acquisition


1. add trial structure acquisition mode
   1. need a protocol feature which specifies which stimuli to use and a file saved which can be viewed and loaded for resuse
   2. would be nice to have a protocol constructor to create a bunch of stimuli, and also specify how long the trial will be, how many times to repeat
   3. should have a toggle button to choose voltage clamp or current clamp so the data can be scaled correctly
      1. this is because ideally i will collect the seal/seal test, the break in, and maybe some voltage steps once broken in. then switch to current clamp for the remainder of the experiment.
   4. if in current clamp trial structure mode, make sure there is a small hyperpolarization before each current injection step protocol to measure access resistance
2. add docs to the repository