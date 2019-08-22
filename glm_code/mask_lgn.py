#!/usr/bin/env python
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
from builtins import str
from builtins import range

import os, datetime, sys

import nipype.interfaces.io as nio           # Data i/o
import nipype.interfaces.fsl as fsl          # fsl
import nipype.interfaces.utility as util     # utility
import nipype.pipeline.engine as pe          # pypeline engine
import nipype.algorithms.modelgen as model   # model generation

from nipype.interfaces.nipy.preprocess import Trim # Trim leading and trailing volumes

import utils # code by AM specific to this project but multiple workflows

fsl.FSLCommand.set_default_output_type('NIFTI_GZ')

#Set up workflow (we assume data has been preprocessed with fmriprep)
masker = pe.Workflow(name='masker')

applymask = pe.MapNode(interface=fsl.ApplyMask(), name="applymask", iterfield=["in_file", "mask_file"])

# Data input and configuration!
BIDSDataGrabber = pe.Node(util.Function(function=utils.get_files, 
      input_names=["subject_id", "session", "task", "raw_data_dir", "preprocessed_data_dir", "space", "run"],
      output_names=["bolds", "masks", "events", "TR", "confounds"]), 
      name="BIDSDataGrabber")

hemi_wf = pe.Workflow(name="masking")

# output
datasink = pe.Node(nio.DataSink(), name='datasink')

masker.connect([
  (applymask, datasink, [('out_file', 'epi_masked')]),
])

hemi_wf.connect([
                    (BIDSDataGrabber, masker, [
                                              ('bolds', 'applymask.in_file'),
                                              ('masks', 'applymask.mask_file')]),
                    ])

if __name__ == '__main__':
    # When this script is invoked from the command line, read in arguments and use them
    # raw data, with event files, appropriate metadata in header, etc
    raw_data_dir = os.path.abspath(sys.argv[1])
    # post-fmriprep BIDS-formatted location
    out_dir = os.path.abspath(sys.argv[2])
    fmriprep_dir = os.path.abspath(os.path.join(out_dir, 'fmriprep'))

    sub = sys.argv[3]
    ses = sys.argv[4]
    task = sys.argv[5]
    space = sys.argv[6]
    if len(sys.argv) > 7:
      import ast # we want to accept a list of runs as a command line option
      run = ast.literal_eval(sys.argv[7]) # build that list from passed-in string representation e.g. "[2, 3, 4]"
    else:
      run = []

    # where intermediate outputs etc are stored
    # by creating a unique one each time, we prevent re-use,
    # which is desirable while testing different processing options and keeping them all
    # but maybe should be changed to a flag or option later (TODO)
    now = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    working_dir = os.path.abspath(os.path.join(out_dir, f"nipype_{sub}_{ses}_{task}"))
    hemi_wf.base_dir = working_dir
    hemi_wf.config = {"execution": {"crashdump_dir": os.path.join(working_dir, 'crashdumps')}}

    BIDSDataGrabber.inputs.raw_data_dir = raw_data_dir
    BIDSDataGrabber.inputs.preprocessed_data_dir = fmriprep_dir
    BIDSDataGrabber.inputs.space = space
    BIDSDataGrabber.inputs.run = run
    BIDSDataGrabber.inputs.subject_id = sub
    BIDSDataGrabber.inputs.session = ses
    BIDSDataGrabber.inputs.task = task

    # manually drawn LGN ROI mask
    applymask.inputs.mask_file = "/Users/smerdis/data/LGN/BIDS/NB_combined/derivatives/sub-NB_R-LGN_mask_manual.nii.gz"

    hemi_wf.write_graph()
    outgraph = hemi_wf.run(plugin='MultiProc', plugin_args={'n_procs':3})
    #outgraph = hemi_wf.run(plugin='Linear') # Easier to debug for the moment