# Utility functions for the LGN-cortical coupling (aka streams) project

import os
import os.path as op

import numpy as np
import pandas as pd

import matplotlib.pyplot as plt


# from fsleyes 0.32
def isBIDSFile(filename, strict=True):
    """Returns ``True`` if ``filename`` looks like a BIDS image or JSON file.

    :arg filename: Name of file to check
    :arg strict:   If ``True`` (the default), the file must be within a BIDS
                   dataset directory, as defined by :func:`inBIDSDir`.
    """
    import re

    name    = os.path.basename(filename)
    pattern = r'([a-z0-9]+-[a-z0-9]+_)*([a-z0-9])+\.(nii|nii\.gz|json)'
    flags   = re.ASCII | re.IGNORECASE
    match   = re.fullmatch(pattern, name, flags) is not None
    
    print(name, match)
    return match

def write_hemifield_localizer_event_file(event_file):
    """Write hemifield localizer event file.
    
    This assumes the run is trimmed before hand,
    with 6 volumes (1 hemicycle) removed up front
    and 1 at the back, leaving 132 = 6 TRs/hemicycle x 22 hemicycles."""
    num_LR_cycles = 11
    len_hemifield_cycle = 13.5
    TR = 2.25

    total_stim_length = (len_hemifield_cycle * 2) * num_LR_cycles
    total_volumes = total_stim_length/TR
    volumes_per_hemicycle = len_hemifield_cycle/TR

    conds = ['R', 'L'] # we start with L, actually, but a hemicycle is trimmed
    print(total_stim_length, volumes_per_hemicycle, total_volumes)

    file_contents = "onset\tduration\ttrial_type\n"
    for hemicycle in range(0, num_LR_cycles*2-1):
        file_contents += f"{(hemicycle)*len_hemifield_cycle}\t{len_hemifield_cycle}\t{conds[hemicycle%len(conds)]}\n"
    
    print(event_file, file_contents, sep="\n")
    
    with open(event_file, 'w') as f:
        f.write(file_contents)

def average_timeseries(bolds, masker):
    """Given a list of bold file names and a NiftiMasker that has already been fit,
    compute the mean across runs of the bold timeseries and return it"""
    for i, bold_file in enumerate(bolds):
        masked_bold_nm = masker.transform(bold_file)
        if i==0: # first run
            all_bolds = np.empty((*masked_bold_nm.shape, len(bolds)))
        print(i, all_bolds.shape, masked_bold_nm.shape, masked_bold_nm.dtype, masked_bold_nm[:5, :5], sep="\n")
        all_bolds[:, :, i] = masked_bold_nm
    return np.average(all_bolds, axis=-1)

def tsv2subjectinfo(events_file, confounds_file=None, exclude=None, trim_indices=None):
    """
    Function to go from events tsv + confounds tsv to subjectinfo,
    which can then be passed to model setup functions.

    events_file, confounds_file: paths to these things
    exclude: trial_types in the events_file to be ignored (does not apply to confounds)
    trim_indices: either none or a tuple that will be used to slice the confounds
                    to conform to the length of the timeseries that is passed to the GLM
                    (TRs/volumes, not seconds)

    TODO: currently the event files are basically handmade, so they artificially reflect
            the (hardcoded) trim values for the hemifield task (6 volumes up front, 1 at the end)
            instead these should be generated by the experiment code and reflect the untrimmed data,
            and then the trimming of fMRI data AND the confounds/events timeseries done in nipype (here)

    SO: for now the trim_indices only apply to the confounds (which reflect the untrimmed data)
        but in the near future they'll be applied to the events as well.
    """
    import numpy as np
    import pandas as pd
    from nipype.interfaces.base import Bunch

    # Events first
    events = pd.read_csv(events_file, sep="\t")
    if exclude is not None:  # not tested
        events.drop(exclude, axis=1, inplace=True)

    conditions = sorted(events['trial_type'].unique())
    onsets = [events['onset'][events['trial_type'] == tt].tolist() for tt in conditions]
    durations = [events['duration'][events['trial_type'] == tt].tolist() for tt in conditions]   
    if 'weight' in events.columns:
      amplitudes = [events['weight'][events['trial_type'] == tt].tolist() for tt in conditions]
    else:
      amplitudes = [np.ones(len(d)) for d in durations]

    # Confounds next
    if confounds_file is None or confounds_file=='':
        regressor_names = []
        regressors = []
    else:
        confounds = pd.read_csv(confounds_file, sep="\t", na_values="n/a") # fmriprep confounds file
        regressor_names=[ # 'white_matter', 'global_signal','framewise_displacement',
                            # 'a_comp_cor_00',
                            # 'a_comp_cor_01',
                            # 'a_comp_cor_02',
                            # 'a_comp_cor_03',
                            # 'a_comp_cor_04',
                            # 'a_comp_cor_05',
                            'trans_x',
                            'trans_y',
                            'trans_z',
                            'rot_x',
                            'rot_y',
                            'rot_z']

        if trim_indices is None:
            regressors = [list(confounds[reg].fillna(0)) for reg in regressor_names]
        else:
            assert(len(trim_indices)==2)
            if trim_indices[-1] == 0: # if nothing is to be trimmed from the end, 0 will be passed in, but slice() wants None
                regressors = [list(confounds[reg].fillna(0))[slice(trim_indices[0], None)] for reg in regressor_names]
            else:
                regressors = [list(confounds[reg].fillna(0))[slice(*trim_indices)] for reg in regressor_names]

    bunch = Bunch(conditions=conditions,
                    onsets=onsets,
                    durations=durations,
                    amplitudes=amplitudes,
                    regressor_names=regressor_names,
                    regressors=regressors)
    
    return bunch

def pickfirst(l):
    return l[0]

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

def fslmaths_threshold_roi_opstring(thresh):
    return [f"-thr {thresh} -bin", f"-uthr {thresh} -bin"]

def get_files(subject_id, session, task, raw_data_dir, preprocessed_data_dir, space=None, run=[], **kwargs):
    """
    Given some information, retrieve all the files and metadata from a
    BIDS-formatted dataset that will be passed to the analysis pipeline.
    """
    from bids import BIDSLayout
    
    # only the raw files have the correct metadata, eg TR, and the event files are here
    raw_layout = BIDSLayout(raw_data_dir, validate=False, derivatives=False)
    preproc_layout = BIDSLayout(preprocessed_data_dir, validate=False)

    subjects = preproc_layout.get_subjects()
    assert subject_id in subjects and subject_id in raw_layout.get_subjects(), "Subject not found!"

    sessions = preproc_layout.get_sessions()
    assert session in sessions, "Session not found!"

    tasks = preproc_layout.get_tasks()
    assert task in tasks, "Task not found!"

    if space=="None":
        space = None

    if space is None:
        print("Space is None")
        bolds = sorted([f for f in preproc_layout.get(subject=subject_id, session=session, task=task, run=run, desc='preproc', suffix='bold',
            extension=['nii.gz'], return_type='file')])
    else:
        bolds = sorted([f for f in preproc_layout.get(subject=subject_id, session=session, task=task, run=run, desc='preproc', suffix='bold',
            extension=['nii.gz'], return_type='file') if f"space-{space}" in f])
    print(f"BOLDS: {len(bolds)}\n{bolds}")
    if space is None:
        masks = sorted([f for f in preproc_layout.get(subject=subject_id, suffix='mask',
            session=session, extension=['nii.gz'], return_type='file')])
        if not masks:
            masks = sorted([f for f in preproc_layout.get(subject=subject_id, suffix='mask',
            session=session, extension=['nii.gz'], return_type='file')])
    else:
        masks = sorted([f for f in preproc_layout.get(subject=subject_id, suffix='mask',
            session=session, extension=['nii.gz'], return_type='file') if f"space-{space}" in f])
        if not masks:
            masks = sorted([f for f in preproc_layout.get(subject=subject_id, suffix='mask',
                session=session, extension=['nii.gz'], return_type='file') if f"space-{space}" in f])
    if len(masks)==1: # there is only one mask and it is to be used for all runs
        masks = masks * len(bolds)
    print(f"Masks: {len(masks)}\n{masks}")
    eventfiles =  sorted(raw_layout.get(subject=subject_id, suffix='events',
                                  task=task, session=session, run=run, extension=['tsv'],
                                  return_type='file'))
    print(f"Eventfiles: {len(eventfiles)}\n{eventfiles}")
    raw_bolds = sorted(raw_layout.get(subject=subject_id, suffix='bold',
                                    task=task, session=session, run=run, extension=['nii.gz'],
                                    return_type='file'))
    TRs = [raw_layout.get_tr(f) for f in raw_bolds]
    print(TRs, len(TRs))
    confounds = sorted(preproc_layout.get(subject=subject_id, desc='confounds', suffix="regressors",
                                  task=task, session=session, run=run, extension=['tsv'], 
                                  return_type='file'))
    print(f"Confounds: {len(confounds)}\n{confounds}")
    if not confounds:
        confounds = ['']*len(bolds)
    #print(list(zip(bolds, masks, eventfiles, TRs)))
    # edit 11/9/18 - remove assert on event files, since some early hemifield scans don't have it
    # but warn!
    if (len(eventfiles) != len(bolds)):
        print("Some functional runs do not have corresponding event files!")
    assert TRs.count(TRs[0])==len(TRs), "Not all TRs are the same!" # all runs for a particular task must have same TR
    assert len(bolds)==len(masks)>0, "Input lists are not the same length!" # used to also check for ==len(confounds)
    TR = TRs[0]
    return bolds, masks, eventfiles, TR, confounds


def get_contrasts(task):
    """
    Setup the contrast structure that needs to be evaluated. This is a list of
    lists. The inner list specifies the contrasts and has the following format -
    [Name,Stat,[list of condition names],[weights on those conditions]. The
    condition names must match the `names` listed in the `subjectinfo` function
    described above.
    """
    if task == "hemi":
        cont_rl = ['R-L', 'T', ['L', 'R'], [-1, 1]]
        cont_lr = ['L-R', 'T', ['L', 'R'], [1, -1]]
        return [cont_rl, cont_lr]
    elif task == "mp":
        cont_mp = ['M-P', 'T', ['M', 'P'], [1, -1]]
        cont_pm = ['P-M', 'T', ['M', 'P'], [-1, 1]]
        cont_visresp = ['P-M', 'T', ['M', 'P'], [1, 1]]
        return [cont_mp, cont_pm, cont_visresp]


def run_fixedeffects_glm(sub, ses, task, run, raw_data_dir, out_dir, working_dir_suffix = None, space = None):
    """Run the fixed effects glm, given some parameters.

    Return the working directory for this glm run"""
    
    import glm_fixedeffects_level12 as glm
    
    if working_dir_suffix is None:
        working_dir = os.path.abspath(os.path.join(os.path.split(out_dir)[0], f"nipype_{sub}_{ses}_{task}"))
    else:
        working_dir = os.path.abspath(os.path.join(os.path.split(out_dir)[0], f"nipype_{sub}_{ses}_{task}_{working_dir_suffix}"))
    glm.BIDSDataGrabber.inputs.raw_data_dir = raw_data_dir
    glm.BIDSDataGrabber.inputs.preprocessed_data_dir = out_dir
    glm.BIDSDataGrabber.inputs.space = space
    glm.BIDSDataGrabber.inputs.run = run
    glm.hemi_wf.base_dir = working_dir
    glm.hemi_wf.config = {"execution": {"crashdump_dir": os.path.join(working_dir, 'crashdumps')}}

    glm.BIDSDataGrabber.inputs.subject_id = sub
    glm.BIDSDataGrabber.inputs.session = ses
    glm.BIDSDataGrabber.inputs.task = task

    contrasts = get_contrasts(task)
    glm.modelfit.inputs.level1design.contrasts = contrasts

    # How many volumes to trim from the functional run before masking and preprocessing
    if task=="mp":
        trim_idxs = (4, 0) # Should be 4 0 for MP
    elif task=="hemi":
        trim_idxs = (6, -1) # 6 at the front, 1 at the back, for hemifield. 
    else:
        assert("Unknown task and thus trim indices!")
    glm.modelfit.inputs.tsv2subjinfo.trim_indices = trim_idxs
    glm.modelfit.inputs.trim.begin_index = trim_idxs[0]
    glm.modelfit.inputs.trim.end_index = trim_idxs[1]

    glm.hemi_wf.write_graph()
    outgraph = glm.hemi_wf.run(plugin='MultiProc', plugin_args={'n_procs':3})
    return working_dir

def get_model_outputs(datasink_dir, contrasts):
    """Given the datasink directory of a glm workflow, this grabs the Level 1 and 2 results for the specified contrasts [list]."""
    import glob
    contents = os.listdir(datasink_dir)
    l1outdir = 'results_dir'
    l2outdir = 'stats_dir'
    #l1tstats = []
    l1copes = []
    l2outs = []
    for contrast_number in contrasts:
        if l1outdir in contents:
            l1glob = os.path.join(datasink_dir, l1outdir, '_modelestimate*')
            l1outputs = sorted(glob.glob(l1glob))
            for l1o in l1outputs:
                #l1tstats.append(os.path.join(l1o, f"results/tstat{contrast_number}.nii.gz"))
                l1copes.append(os.path.join(l1o, f"results/cope{contrast_number}.nii.gz"))
                
        if l2outdir in contents:
            l2contrastdir = os.path.join(datasink_dir, l2outdir, f"_flameo{contrast_number - 1}", "stats")
            l2outs.extend([os.path.join(l2contrastdir, x) for x in ['cope1.nii.gz']])
        
    return l1copes, l2outs

def view_results(datasink_dir, contrast_number, anat, func, vROI=''):
    """Prints an fsleyes command to view results of L1/2 for a given contrast.
    Allows specification of anat, func, and optional other ROI image."""
    c, l2 = get_model_outputs(datasink_dir, contrast_number)
    # print(f"fsleyes {anat} {func} {vROI} {' '.join(c)} {' '.join(l2)}")
    return f"fsleyes {anat} {func} {vROI} {' '.join(c)} {' '.join(l2)}"

def roi_stats(roi_dict, ref_vol_img):
    M = ref_vol_img.affine[:3, :3]
    abc = ref_vol_img.affine[:3, 3]
    for label, roi in roi_dict.items():
        print(f"{label}")
        coords = np.argwhere(roi.get_fdata()!=0)
        print(coords.shape)
        roi_center = np.mean(coords, 0)
        roi_max_bounds = np.max(coords, 0)
        roi_min_bounds = np.min(coords, 0)
        print(roi_max_bounds, roi_min_bounds)
        print(roi_max_bounds-roi_min_bounds, roi_max_bounds-roi_center)
        print(roi_center, M.dot(roi_center) + abc)
        print("****")

def assign_roi_percentile(roi, beta_map, cut_pct, ref_vol_img, which_hemi, roi_below_suffix='P', roi_above_suffix='M'):
    from nilearn.image import load_img, threshold_img, math_img
    from nilearn.input_data import NiftiMasker

    # first, figure out what the filenames of the new ROIs will be
    roi_stub = op.basename(roi).split('.')[0]
    roi_filename_parts = roi_stub.split('_')[:-2]
    roi_dir = op.dirname(roi)
    roi_below_name = f"{roi_stub.split('_')[-2]}{roi_below_suffix}{cut_pct}"
    roi_above_name = f"{roi_stub.split('_')[-2]}{roi_above_suffix}{cut_pct}"
    roi_below_filename = f"{op.join(roi_dir, '_'.join([*roi_filename_parts, roi_below_name, 'roi']))}.nii.gz"
    roi_above_filename = f"{op.join(roi_dir, '_'.join([*roi_filename_parts, roi_above_name, 'roi']))}.nii.gz"
    print(f"Given the LGN mask \n{roi}\nwill partition at {cut_pct}% and create\n{roi_below_filename}\n{roi_above_filename}", sep='\n')

    # mask the beta map with the roi mask
    beta_masker = NiftiMasker(mask_img=roi)
    roi_betas = beta_masker.fit_transform(beta_map)[0]

    # plot histogram of beta values within roi mask
    plt.hist(roi_betas, bins=24)
    plt.xlabel("BetaM-P")
    plt.ylabel("Number of voxels")
    threshold = np.percentile(roi_betas, cut_pct) # value above/below which voxels are assigned to different ROIs
    print(f"Mask contains {len(roi_betas)} voxels and {cut_pct}th percentile is {threshold}")
    plt.axvline(x=threshold, color="orange")
    plt.show()
    plt.close()

    # threshold at value determined by percentile testing above
    roi_nilearn = threshold_img(beta_map, threshold=threshold, mask_img=roi)
    # threshold only works one way, returning values above the threshold
    # therefore to get values below it, we do (-img)>(-threshold) within the roi mask
    roi_nilearn_neg = threshold_img(math_img(f"-img", img=beta_map), threshold=(-1*threshold), mask_img=roi)
    # exclude the zero voxels (not in the roi mask)
    above_mask = math_img(f"np.logical_and((img != 0), (img > {threshold}))", img=roi_nilearn)
    below_mask = math_img(f"np.logical_and((img != 0), (img > {-1*threshold}))", img=roi_nilearn_neg)
    n_vox_above = np.count_nonzero(above_mask.get_data())
    n_vox_below = np.count_nonzero(below_mask.get_data())

    # save files
    above_mask.to_filename(roi_above_filename)
    below_mask.to_filename(roi_below_filename)
    print(f"{roi_above_filename}: {n_vox_above} voxels\n{roi_below_filename}: {n_vox_below} voxels")
    # we should have assigned all voxels in the ROI mask to one of the two new ROIs
    assert(len(roi_betas)==n_vox_above+n_vox_below)
    
    # fix/change this to not assume M/P
    imgs_in_order = [load_img(roi_below_filename), load_img(roi_above_filename), load_img(beta_map)] #[load_img(roi_above_filename), load_img(roi_below_filename), beta_map]
    r = 5
    c = 5
    fig, ax = plt.subplots(ncols=r, figsize=(16,10))
    img_data = [img.get_data() for img in imgs_in_order]
    p_roi, m_roi, beta_mp = img_data
    print([np.count_nonzero(x) for x in img_data])
    p_roi_masked = np.ma.masked_where(p_roi==0, p_roi)
    m_roi_masked = np.ma.masked_where(m_roi==0, m_roi)
    p_mask = np.ma.getmask(p_roi_masked)
    m_mask = np.ma.getmask(m_roi_masked)
    both_mask = np.logical_and(p_mask,m_mask)
    beta_masked = np.ma.masked_array(beta_mp, mask=both_mask)
    print([np.count_nonzero(~m) for m in [p_mask, m_mask, both_mask]])
    ref_vol_data = ref_vol_img.get_data()
    for ri in range(r):
        if which_hemi == 'L':
            ax[ri].imshow(ref_vol_data[64:84,50:70,14-ri], cmap="gray_r")
            pos = ax[ri].imshow(beta_masked[64:84,50:70,13-ri], cmap="coolwarm")
        elif which_hemi == 'R':
            ax[ri].imshow(ref_vol_data[40:64,50:70,14-ri], cmap="gray_r")
            pos = ax[ri].imshow(beta_masked[40:64,50:70,13-ri], cmap="coolwarm")
        ax[ri].set_xticks([])
        ax[ri].set_yticks([])
        #ax[ri].set_ylabel("")
        plt.colorbar(pos, ax=ax[ri])
        plt.subplots_adjust(wspace=.1)
    plt.show()
    plt.close()
    return above_mask, below_mask, threshold