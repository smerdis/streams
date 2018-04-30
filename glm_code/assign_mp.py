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

import utils # code by AM specific to this project but multiple workflows

fsl.FSLCommand.set_default_output_type('NIFTI_GZ')

#Set up model fitting workflow (we assume data has been preprocessed with fmriprep)
wf = pe.Workflow(name='assignmp')

applymask = pe.Node(interface=fsl.ApplyMask(), name="applymask", input_names=["in_file", "mask_file"])
get_percentile_threshold = pe.Node(interface=fsl.ImageStats(), name="get_percentile_threshold", input_names=["in_file", "op_string"])
assign_voxels = pe.MapNode(interface=fsl.ImageMaths(), name="assign_voxels", iterfield=["op_string"])

wf.connect([
    (applymask, get_percentile_threshold, [('out_file', 'in_file')]),
    (applymask, assign_voxels, [('out_file', 'in_file')]),
    (get_percentile_threshold, assign_voxels, [(('out_stat', utils.fslmaths_threshold_roi_opstring), 'op_string')]),
])

# Data input and configuration!
infosource = pe.Node(util.IdentityInterface(fields=['cope_file', 'roi_mask', 'percentile_threshold', 'out_files']),
        name="infosource", output_names=["rois"])
# output
datasink = pe.Node(nio.DataSink(), name='datasink')

wf.connect([
    (infosource, applymask, [('cope_file', 'in_file'),
                             ('roi_mask', 'mask_file')]),
    (infosource, get_percentile_threshold, [('percentile_threshold', 'op_string')]),
    (infosource, assign_voxels, [('out_files', 'out_file')]),
    (assign_voxels, datasink, [('out_file', 'roi_mask')]),
    ])

if __name__ == '__main__':
    # When this script is invoked from the command line, read in arguments and use them
    cope_file = os.path.abspath(sys.argv[1])
    roi_mask = os.path.abspath(sys.argv[2])

    pct_thresh = sys.argv[3]
    fslstats_op_string = f"-P {pct_thresh}"

    roi_below_name = sys.argv[4]
    roi_above_name = sys.argv[5]

    out_dir = os.path.abspath(sys.argv[6])

    # where intermediate outputs etc are stored
    # by creating a unique one each time, we prevent re-use,
    # which is desirable while testing different processing options and keeping them all
    # but maybe should be changed to a flag or option later (TODO)
    now = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    working_dir = os.path.abspath(os.path.join(out_dir, f"assignmp_{now}"))
    wf.base_dir = working_dir
    wf.config = {"execution": {"crashdump_dir": os.path.join(working_dir, 'crashdumps')}}

    infosource.inputs.cope_file = cope_file
    infosource.inputs.roi_mask = roi_mask
    infosource.inputs.percentile_threshold = fslstats_op_string

    wf.write_graph()
    outgraph = wf.run(plugin='MultiProc', plugin_args={'n_procs':3})