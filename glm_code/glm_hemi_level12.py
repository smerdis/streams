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

import utils

fsl.FSLCommand.set_default_output_type('NIFTI_GZ')

#Set up model fitting workflow (we assume preprocessing has been done separately)
modelfit = pe.Workflow(name='modelfit')

#Custom interface wrapping function Tsv2subjectinfo
#inputs are defined below in "experiment-specific details"
tsv2subjinfo = pe.MapNode(util.Function(function=utils.tsv2subjectinfo, input_names=['in_file'],
                         output_names=['subject_info']), name="tsv2subjinfo", iterfield=['in_file'])

"""
Use :class:`nipype.algorithms.modelgen.SpecifyModel` to generate design information.
"""

modelspec = pe.MapNode(interface=model.SpecifyModel(), name="modelspec", iterfield=['subject_info'])

"""
Use :class:`nipype.interfaces.fsl.Level1Design` to generate a run specific fsf
file for analysis
"""

level1design = pe.MapNode(interface=fsl.Level1Design(), name="level1design", iterfield=['session_info'])

"""
Use :class:`nipype.interfaces.fsl.FEATModel` to generate a run specific mat
file for use by FILMGLS
"""

modelgen = pe.MapNode(interface=fsl.FEATModel(), name='modelgen', iterfield=["fsf_file", "ev_files"])


"""
Use :class:`nipype.interfaces.fsl.FILMGLS` to estimate a model specified by a
mat file and a functional run
"""

modelestimate = pe.MapNode(interface=fsl.FILMGLS(smooth_autocorr=True,
                                                 mask_size=5,
                                                 threshold=1000),
                           name='modelestimate',
                           iterfield=['design_file', 'in_file', 'tcon_file'])

modelfit.connect([
    (tsv2subjinfo, modelspec, [('subject_info', 'subject_info')]),
    (modelspec, level1design, [('session_info', 'session_info')]),
    (level1design, modelgen, [('fsf_files', 'fsf_file'),
                              ('ev_files', 'ev_files')]),
    (modelgen, modelestimate, [('design_file', 'design_file'),
                              ('con_file','tcon_file')])
    ])

"""
Set up first-level workflow
---------------------------

"""
def sort_copes(files):
    numelements = len(files[0])
    outfiles = []
    for i in range(numelements):
        outfiles.insert(i, [])
        for j, elements in enumerate(files):
            outfiles[i].append(elements[i])
    return outfiles


def num_copes(files):
    return len(files)


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

contrasts = utils.get_hemifield_contrasts()

modelfit.inputs.modelspec.input_units = 'secs'
modelfit.inputs.modelspec.high_pass_filter_cutoff = 128.

modelfit.inputs.level1design.bases = {'dgamma': {'derivs': False}}
modelfit.inputs.level1design.contrasts = contrasts
modelfit.inputs.level1design.model_serial_correlations = True

"""
Set up complete workflow
========================
"""

hemi_wf = pe.Workflow(name="hemifield_localizer")
hemi_wf.base_dir = os.path.abspath('./fsl/workingdir')
hemi_wf.config = {"execution": {"crashdump_dir": os.path.abspath('./fsl/crashdumps')}}

datasink = pe.Node(nio.DataSink(), name='datasink')

modelfit.connect([
  (modelgen, datasink, [('design_image', 'design_image'), ('design_file', 'design_file')]),
  (modelestimate, datasink, [('results_dir', 'results_dir')])
])

hemi_wf.connect([
                    (BIDSDataGrabber, modelfit, [('events', 'tsv2subjinfo.in_file'),
                                              ('bolds', 'modelspec.functional_runs'),
                                              ('bolds', 'modelestimate.in_file'),
                                              ('TR', 'modelspec.time_repetition'),
                                              ('TR', 'level1design.interscan_interval')])
                    ])

if __name__ == '__main__':
    hemi_wf.write_graph()
    #outgraph = hemi_wf.run(plugin='MultiProc', plugin_args={'n_procs':3})
    outgraph = hemi_wf.run(plugin='Linear') # Easier to debug for the moment