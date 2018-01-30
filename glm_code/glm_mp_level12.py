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
import nipype.algorithms.rapidart as ra      # artifact detection

fsl.FSLCommand.set_default_output_type('NIFTI_GZ')

#Set up model fitting workflow (we assume preprocessing has been done separately)
modelfit = pe.Workflow(name='modelfit')

# Function to go from events tsv to subjectinfo
def tsv2subjectinfo(in_file):

    import pandas as pd
    from nipype.interfaces.base import Bunch
    import numpy as np

    events = pd.read_csv(in_file, sep=str('\t'))

    #if exclude is not None:  # not tested
    #    events.drop(exclude, axis=1, inplace=True)

    conditions = sorted(events['trial_type'].unique())
    onsets = [events['onset'][events['trial_type'] == tt].tolist() for tt in conditions]
    durations = [events['duration'][events['trial_type'] == tt].tolist() for tt in conditions]
    
    if 'weight' in events.columns:
      amplitudes = [events['weight'][events['trial_type'] == tt].tolist() for tt in conditions]
    else:
      amplitudes = [np.ones(len(d)) for d in durations]

    bunch = Bunch(conditions=conditions,
                  onsets=onsets,
                  durations=durations,
                  amplitudes=amplitudes)
    
    return bunch

#Custom interface wrapping function Tsv2subjectinfo
#inputs are defined below in "experiment-specific details"
tsv2subjinfo = pe.MapNode(util.Function(function=tsv2subjectinfo, input_names=['in_file'],
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
Set up fixed-effects workflow
-----------------------------

"""

#fixed_fx = pe.Workflow(name='fixedfx')

copemerge = pe.MapNode(interface=fsl.Merge(dimension='t'),
                          iterfield=['in_files'],
                          name="copemerge")

varcopemerge = pe.MapNode(interface=fsl.Merge(dimension='t'),
                       iterfield=['in_files'],
                       name="varcopemerge")

maskemerge = pe.MapNode(interface=fsl.Merge(dimension='t'),
                       iterfield=['in_files'],
                       name="maskemerge")

pickfirst = lambda x: x[0]

"""
Use :class:`nipype.interfaces.fsl.L2Model` to generate subject and condition
specific level 2 model design files
"""

level2model = pe.Node(interface=fsl.L2Model(),
                      name='l2model')

"""
Use :class:`nipype.interfaces.fsl.FLAMEO` to estimate a second level model
"""

flameo = pe.MapNode(interface=fsl.FLAMEO(run_mode='fe'), name="flameo",
                    iterfield=['cope_file', 'var_cope_file'])

modelfit.connect([(copemerge, flameo, [('merged_file', 'cope_file')]),
                  (varcopemerge, flameo, [('merged_file', 'var_cope_file')]),
                  (level2model, flameo, [('design_mat', 'design_file'),
                                         ('design_con', 't_con_file'),
                                         ('design_grp', 'cov_split_file')]),
                  (maskemerge, flameo, [(('merged_file',pickfirst),'mask_file')])
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

#modelfit = pe.Workflow(name='modelfit')
modelfit.connect([
                    (modelestimate, copemerge, [(('copes', sort_copes), 'in_files')]),
                    (modelestimate, varcopemerge, [(('varcopes', sort_copes), 'in_files')]),
                    (modelestimate, level2model, [(('copes', num_copes), 'num_copes')])
                  ])


"""
Experiment specific components
------------------------------

"""

# let's start from BidsDataGrabber again
def get_files(subject_id, session, raw_data_dir, preprocessed_data_dir):
    # Remember that all the necesary imports need to be INSIDE the function 
    # for the Function Interface to work!
    from bids.grabbids import BIDSLayout
    
    raw_layout = BIDSLayout(raw_data_dir)
    preproc_layout = BIDSLayout(preprocessed_data_dir)

    subjects = preproc_layout.get_subjects()
    assert(subject_id in subjects)

    tasks = preproc_layout.get_tasks()
    assert("mp" in tasks)

    sessions = preproc_layout.get_sessions()
    assert(session in sessions)

    print(subjects, tasks, sessions)
    
    bolds = [f.filename.replace(raw_data_dir, preprocessed_data_dir).replace('_bold.', '_bold_space-T1w_preproc.') for f in raw_layout.get(subject=subject_id,
      type="bold", modality="func", task="mp", session=session, 
      extensions=['nii.gz'])]

    masks = [b.replace('_preproc', '_brainmask') for b in bolds]

    eventfiles =  [f.filename for f in raw_layout.get(subject=subject_id,
      modality="func", task="mp", session=session, 
      extensions=['tsv'])]

    TR = [raw_layout.get_metadata(f.filename)['RepetitionTime'] for f in raw_layout.get(subject=subject_id,
      type="bold", modality="func", task="mp", session=session, 
      extensions=['nii.gz'])][0]
    
    print(bolds, eventfiles, TR)

    return bolds, masks, eventfiles, TR


BIDSDataGrabber = pe.Node(util.Function(function=get_files, 
                                    input_names=["subject_id", "session", "raw_data_dir", "preprocessed_data_dir"],
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

#modelfit.inputs.tsv2subjinfo.exclude = None # don't exclude any event types (only m/p listed)

"""
Setup the contrast structure that needs to be evaluated. This is a list of
lists. The inner list specifies the contrasts and has the following format -
[Name,Stat,[list of condition names],[weights on those conditions]. The
condition names must match the `names` listed in the `subjectinfo` function
described above.
"""

#cont1 = ['Task>Baseline', 'T', ['Task-Odd', 'Task-Even'], [0.5, 0.5]]
#cont2 = ['Task-Odd>Task-Even', 'T', ['Task-Odd', 'Task-Even'], [1, -1]]
#cont3 = ['Task', 'F', [cont1, cont2]]
cont_mp = ['M-P', 'T', ['M', 'P'], [1, -1]]
cont_pm = ['P-M', 'T', ['M', 'P'], [-1, 1]]
cont_visresp = ['Task>Baseline', 'T', ['M', 'P'], [0.5, 0.5]]
contrasts = [cont_mp, cont_pm, cont_visresp]

modelfit.inputs.modelspec.input_units = 'secs'
modelfit.inputs.modelspec.high_pass_filter_cutoff = 128.

modelfit.inputs.level1design.bases = {'dgamma': {'derivs': False}}
modelfit.inputs.level1design.contrasts = contrasts
modelfit.inputs.level1design.model_serial_correlations = True

"""
Set up complete workflow
========================
"""

l1pipeline = pe.Workflow(name="level1")
l1pipeline.base_dir = os.path.abspath('./fsl/workingdir')
l1pipeline.config = {"execution": {"crashdump_dir": os.path.abspath('./fsl/crashdumps')}}

datasink = pe.Node(nio.DataSink(), name='datasink')
modelfit.connect([
  (modelgen, datasink, [('design_image', 'design_image'),
                        ('design_file', 'design_file')]),
  (modelestimate, datasink, [('results_dir', 'results_dir')]),
  (flameo, datasink, [('stats_dir', 'stats_dir')])
  ])

l1pipeline.connect([
                    (BIDSDataGrabber, modelfit, [('events', 'tsv2subjinfo.in_file'),
                                              ('bolds', 'modelspec.functional_runs'),
                                              ('bolds', 'modelestimate.in_file'),
                                              ('TR', 'modelspec.time_repetition'),
                                              ('TR', 'level1design.interscan_interval'),
                                              ('masks', 'maskemerge.in_files')])
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
    l1pipeline.write_graph()
    outgraph = l1pipeline.run(plugin='MultiProc', plugin_args={'n_procs':3})
    # l1pipeline.run(plugin='MultiProc', plugin_args={'n_procs':2})
