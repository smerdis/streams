#!/usr/bin/env python
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
from builtins import str
from builtins import range

import os                                    # system functions

import nipype.interfaces.io as nio           # Data i/o
import nipype.interfaces.fsl as fsl          # fsl
import nipype.interfaces.utility as util     # utility
import nipype.pipeline.engine as pe          # pypeline engine
import nipype.algorithms.modelgen as model   # model generation

from nipype.interfaces.nipy.preprocess import Trim # Trim leading and trailing volumes

import utils

fsl.FSLCommand.set_default_output_type('NIFTI_GZ')

#Set up model fitting workflow (we assume data has been preprocessed with fmriprep)
modelfit = pe.Workflow(name='modelfit')

#Custom interface wrapping function Tsv2subjectinfo
tsv2subjinfo = pe.MapNode(util.Function(function=utils.tsv2subjectinfo, input_names=['in_file'],
                         output_names=['subject_info']), name="tsv2subjinfo", iterfield=['in_file'])
modelspec = pe.MapNode(interface=model.SpecifyModel(), name="modelspec", iterfield=['subject_info'])
level1design = pe.MapNode(interface=fsl.Level1Design(), name="level1design", iterfield=['session_info'])
modelgen = pe.MapNode(interface=fsl.FEATModel(), name='modelgen', iterfield=["fsf_file", "ev_files"])

trim = pe.MapNode(interface=Trim(), name="trim", iterfield=['in_file'])
applymask = pe.MapNode(interface=fsl.ApplyMask(), name="applymask", iterfield=["in_file", "mask_file"])

modelestimate = pe.MapNode(interface=fsl.FILMGLS(), name='modelestimate',
                        iterfield=['design_file', 'in_file', 'tcon_file'])

modelfit.connect([
    (tsv2subjinfo, modelspec, [('subject_info', 'subject_info')]),
    (trim, modelspec, [('out_file', 'functional_runs')]),
    (modelspec, level1design, [('session_info', 'session_info')]),
    (level1design, modelgen, [('fsf_files', 'fsf_file'),
                              ('ev_files', 'ev_files')]),
    (modelgen, modelestimate, [('design_file', 'design_file'),
                              ('con_file','tcon_file')]),
    (trim, applymask, [('out_file', 'in_file')]),
    (applymask, modelestimate, [('out_file', 'in_file')])
    ])

# Data input and configuration!
BIDSDataGrabber = pe.Node(util.Function(function=utils.get_files, 
                                    input_names=["subject_id", "session", "task", "raw_data_dir", "preprocessed_data_dir"],
                                    output_names=["bolds", "masks", "events", "TR"]), 
                           name="BIDSDataGrabber")

# Specify the location of the data.
# raw data, with event files, appropriate metadata in header, etc
raw_data_dir = os.path.abspath('/Users/smerdis/data/LGN/BIDS/AlexLGN_no0327/')
# preprocessed data - these are the files that should be modeled
preprocessed_data_dir = os.path.abspath('/Users/smerdis/data/LGN/BIDS/AlexLGN_no0327_out_T1w/fmriprep/')
# where intermediate outputs etc are stored
working_dir = os.path.abspath('/Users/smerdis/data/LGN/BIDS/AlexLGN_no0327_out_T1w/nipype/')

BIDSDataGrabber.inputs.raw_data_dir = raw_data_dir
BIDSDataGrabber.inputs.preprocessed_data_dir = preprocessed_data_dir
BIDSDataGrabber.inputs.subject_id='MS'
BIDSDataGrabber.inputs.session='20150401'
BIDSDataGrabber.inputs.task='hemi'

contrasts = utils.get_hemifield_contrasts()

# How many volumes to trim from the functional run before masking and preprocessing
modelfit.inputs.trim.begin_index = 6
modelfit.inputs.trim.end_index = -1

modelfit.inputs.modelspec.input_units = 'secs'
modelfit.inputs.modelspec.high_pass_filter_cutoff = 128.

modelfit.inputs.level1design.bases = {'dgamma': {'derivs': False}}
modelfit.inputs.level1design.contrasts = contrasts
modelfit.inputs.level1design.model_serial_correlations = True
modelfit.inputs.level1design.interscan_interval = modelfit.inputs.modelspec.time_repetition

modelfit.inputs.modelestimate.smooth_autocorr = True
modelfit.inputs.modelestimate.mask_size = 5
modelfit.inputs.modelestimate.threshold = 10

hemi_wf = pe.Workflow(name="hemifield_localizer")
hemi_wf.base_dir = working_dir
hemi_wf.config = {"execution": {"crashdump_dir": os.path.join(working_dir, 'crashdumps')}}

# output
datasink = pe.Node(nio.DataSink(), name='datasink')

modelfit.connect([
  (modelgen, datasink, [('design_image', 'design_image'), ('design_file', 'design_file')]),
  (modelestimate, datasink, [('results_dir', 'results_dir')]),
  (applymask, datasink, [('out_file', 'epi_masked_trimmed')])
])

hemi_wf.connect([
                    (BIDSDataGrabber, modelfit, [('events', 'tsv2subjinfo.in_file'),
                                              ('bolds', 'trim.in_file'),
                                              ('masks', 'applymask.mask_file'),
                                              ('TR', 'modelspec.time_repetition'),
                                              ('TR', 'level1design.interscan_interval')])
                    ])

if __name__ == '__main__':
    hemi_wf.write_graph()
    outgraph = hemi_wf.run(plugin='MultiProc', plugin_args={'n_procs':3})
    #outgraph = hemi_wf.run(plugin='Linear') # Easier to debug for the moment