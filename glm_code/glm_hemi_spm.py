#!/usr/bin/env python
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
from builtins import str
from builtins import range

import os                                    # system functions

import nipype.interfaces.io as nio           # Data i/o
import nipype.interfaces.spm as spm
#from nipype.interfaces.fsl import BET
import nipype.interfaces.utility as util     # utility
import nipype.pipeline.engine as pe          # pypeline engine
import nipype.algorithms.modelgen as model   # model generation

import utils

#Set up model fitting workflow (we assume preprocessing has been done separately)
modelfit = pe.Workflow(name='modelfit')

#Custom interface wrapping function Tsv2subjectinfo
#inputs are defined below in "experiment-specific details"
tsv2subjinfo = pe.MapNode(util.Function(function=utils.tsv2subjectinfo, input_names=['in_file'],
                         output_names=['subject_info']), name="tsv2subjinfo", iterfield=['in_file'])

modelspec = pe.MapNode(interface=model.SpecifySPMModel(), name="modelspec", iterfield=['subject_info'])

level1design = pe.MapNode(interface=spm.Level1Design(), name="level1design", iterfield=['session_info'])

#skullstrip = pe.MapNode(interface=fsl.BET(), name="skullstrip", iterables=['in_file'])
#skullstrip.inputs.mask = True

modelestimate = pe.MapNode(interface=spm.EstimateModel(), name='modelestimate',
                           iterfield=['spm_mat_file'])

contrastestimate = pe.MapNode(interface=spm.EstimateContrast(), name="contrastestimate",
                          iterfield=['spm_mat_file', 'beta_images', 'residual_image'])

threshold = pe.MapNode(interface=spm.Threshold(), name='threshold', iterfield=['spm_mat_file', 'stat_image'])
#threshold.inputs.use_fwe_correction = True
threshold.inputs.extent_fdr_p_threshold = 0.05

modelfit.connect([
    (tsv2subjinfo, modelspec, [('subject_info', 'subject_info')]),
    (modelspec, level1design, [('session_info', 'session_info')]),
    #(skullstrip, level1design, [('mask_file', 'mask_image')])
    (level1design, modelestimate, [('spm_mat_file', 'spm_mat_file')]),
    (modelestimate, contrastestimate, [('spm_mat_file', 'spm_mat_file'), ('beta_images', 'beta_images'),
      ('residual_image', 'residual_image')]),
    (contrastestimate, threshold, [('spm_mat_file', 'spm_mat_file'),
                                   (('spmT_images', utils.pickfirst),
                                    'stat_image')]),
    ])

"""
Experiment specific components
------------------------------

"""

BIDSDataGrabber = pe.Node(util.Function(function=utils.get_files, 
                                    input_names=["subject_id", "session", "task", "raw_data_dir", "preprocessed_data_dir"],
                                    output_names=["bolds", "masks", "events", "TR"]), 
                           name="BIDSDataGrabber")

# Specify the location of the data.
# raw data, with event files, appropriate metadata in header, etc
raw_data_dir = os.path.abspath('/Users/smerdis/data/LGN/BIDS/AlexLGN_no0327/')
# preprocessed data - these are the files that should be modeled
preprocessed_data_dir = os.path.abspath('/Users/smerdis/data/LGN/BIDS/AlexLGN_no0327_out_T1w/fmriprep/')

BIDSDataGrabber.inputs.raw_data_dir = raw_data_dir
BIDSDataGrabber.inputs.preprocessed_data_dir = preprocessed_data_dir
BIDSDataGrabber.inputs.subject_id='MS'
BIDSDataGrabber.inputs.session='20150401'
BIDSDataGrabber.inputs.task='hemi'

modelfit.inputs.modelspec.concatenate_runs = False
modelfit.inputs.modelspec.input_units = 'secs'
modelfit.inputs.modelspec.output_units = 'secs'
modelfit.inputs.modelspec.high_pass_filter_cutoff = 128

modelfit.inputs.level1design.bases = bases = {'hrf': {'derivs': [0, 0]}}
modelfit.inputs.level1design.timing_units = 'secs'
modelfit.inputs.level1design.model_serial_correlations = 'none'
modelfit.inputs.level1design.use_mcr = False
modelfit.inputs.level1design.matlab_cmd = '/Applications/MATLAB_R2017a.app/bin/matlab'

modelfit.inputs.modelestimate.estimation_method = {'Classical': 1}

"""
Setup the contrast structure that needs to be evaluated. This is a list of
lists. The inner list specifies the contrasts and has the following format -
[Name,Stat,[list of condition names],[weights on those conditions]. The
condition names must match the `names` listed in the `subjectinfo` function
described above.
"""

cont_lr = ['L-R', 'T', ['L', 'R'], [1, -1]]
cont_rl = ['R-L', 'T', ['L', 'R'], [-1, 1]]
cont_visresp = ['Task>Baseline', 'T', ['L', 'R'], [0.5, 0.5]]
contrasts = [cont_lr, cont_rl, cont_visresp]
modelfit.inputs.contrastestimate.contrasts = contrasts

"""
Set up complete workflow
========================
"""

hemi_wf = pe.Workflow(name="hemifield_localizer_spm")
hemi_wf.base_dir = os.path.abspath('./hemifield_localizer_spm/workingdir')
hemi_wf.config = {"execution": {"crashdump_dir": os.path.abspath('./hemifield_localizer_spm/crashdumps')}}

datasink = pe.Node(nio.DataSink(), name='datasink')

modelfit.connect([
  (modelestimate, datasink, [('spm_mat_file', 'spm_mat_file'), ('beta_images', 'beta_images'),
      ('residual_image', 'residual_image')]),
  (contrastestimate, datasink, [(('spmT_images', utils.pickfirst),'stat_image')]),
  (threshold, datasink, [('thresholded_map', 'thresholded_map')])
])

hemi_wf.connect([
                    (BIDSDataGrabber, modelfit, [('events', 'tsv2subjinfo.in_file'),
                                              ('bolds', 'modelspec.functional_runs'),
                                              (('masks', utils.pickfirst), 'level1design.mask_image'),
                                              ('TR', 'modelspec.time_repetition'),
                                              ('TR', 'level1design.interscan_interval')])
                    ])

"""
Execute the pipeline
--------------------

The code discussed above sets up all the necessary data structures with
appropriate parameters and the connectivity between the processes, but does not
generate any output. To actually run the analysis on the data the
``nipype.pipeline.engine.Pipeline.Run`` function needs to be called.
"""

if __name__ == '__main__':
    hemi_wf.write_graph()
    outgraph = hemi_wf.run()
    #outgraph = hemi_wf.run(plugin='MultiProc', plugin_args={'n_procs':3})
    # hemi_wf.run(plugin='MultiProc', plugin_args={'n_procs':2})