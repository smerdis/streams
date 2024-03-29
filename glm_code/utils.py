# Utility functions for the LGN-cortical coupling (aka streams) project

import os, glob
import os.path as op

import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
consoleHandler = logging.StreamHandler()
consoleHandler.setLevel(logging.DEBUG)
fileHandler = logging.FileHandler('sub-LL_analysis.log')
fileHandler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
consoleHandler.setFormatter(formatter)
fileHandler.setFormatter(formatter)
logger.addHandler(consoleHandler)
logger.addHandler(fileHandler)
logger.setLevel(logging.DEBUG)

import numpy as np
import pandas as pd

import matplotlib.pyplot as plt
import matplotlib.colors as colors

import nibabel as nib

import nilearn
from nilearn.masking import apply_mask
from nilearn.plotting import plot_img, plot_epi, plot_roi, plot_stat_map, view_img, plot_anat
from nilearn.image import get_data, index_img, load_img, threshold_img, math_img, resample_to_img, new_img_like, coord_transform
from nilearn.input_data import NiftiMasker

import nitime
import nitime.fmri.io as nfio
import nitime.timeseries as ts
import nitime.analysis as nta
import nitime.utils as ntu
import nitime.viz as ntv

## functions for GLM implementation in nipype
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

def get_files(subject_id, session, task, raw_data_dir, preprocessed_data_dir, space=None, run=[], strict=True, **kwargs):
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
        bolds = sorted([f for f in preproc_layout.get(subject=subject_id, session=session, task=task, run=run, suffix='bold',
            extension=['nii.gz'], return_type='file')])
    else:
        bolds = sorted([f for f in preproc_layout.get(subject=subject_id, session=session, task=task, run=run, suffix='bold',
            extension=['nii.gz'], return_type='file') if f"space-{space}" in f])
    print(f"BOLDS: {len(bolds)}\n{bolds}")
    if space is None:
        masks = sorted([f for f in preproc_layout.get(subject=subject_id, suffix='mask',
            session=session, task=task, extension=['nii.gz'], return_type='file')])
        if not masks:
            masks = sorted([f for f in preproc_layout.get(subject=subject_id, suffix='mask',
            session=session, extension=['nii.gz'], return_type='file')])
    else:
        masks = sorted([f for f in preproc_layout.get(subject=subject_id, suffix='mask',
            session=session, task=task, extension=['nii.gz'], return_type='file') if f"space-{space}" in f])
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
    TRs = [raw_layout.get_metadata(f)['RepetitionTime'] for f in raw_bolds]
    print(TRs, len(TRs))
    confounds = sorted(preproc_layout.get(subject=subject_id, suffix="regressors",
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
    if strict:
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


def run_fixedeffects_glm(sub, ses, task, run, raw_data_dir, out_dir, working_dir_suffix = None, space = None, **kwargs):
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
    try:
        trim_idxs = kwargs.get('trim_idxs')
        assert(trim_idxs is not None)
    except KeyError:
        print("Trim indices not provided, will use defaults")
        if task=="mp":
            trim_idxs = (4, 0) # Should be 4 0 for MP when 139 2.25s TRs acquired
        elif task=="hemi":
            trim_idxs = (6, -1) # 6 at the front, 1 at the back, for hemifield. 

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


## Functions for dealing with rois

def roi_map_scatter(roi, beta_map, ref_vol_img):
    # get coordinates of roi, calculate bounds
    coords = np.argwhere(load_img(roi).get_fdata()!=0)
    roi_max_bounds = np.max(coords, 0)
    roi_min_bounds = np.min(coords, 0)
    roi_center_native = np.mean(coords, 0)
    roi_center_ras = coord_transform(*roi_center_native, ref_vol_img.affine)

    print(f"****\n{roi} extends from {roi_max_bounds} to {roi_min_bounds} and is centered at:\n{roi_center_native} (native) = {roi_center_ras} (RAS)", sep='\n')

    # mask the beta map with the roi mask
    beta_masker = NiftiMasker(mask_img=roi)
    roi_betas = beta_masker.fit_transform(beta_map)[0]
    roi_beta_min = np.min(roi_betas)
    roi_beta_max = np.max(roi_betas)

    print(coords.shape, roi_betas.shape)
    coords_ras = np.array(coord_transform(coords[:, 0], coords[:, 1], coords[:, 2], ref_vol_img.affine))

    plt.scatter(coords_ras[0, :], roi_betas)
    plt.xlabel("Left - Right")
    plt.ylabel(f"{op.basename(beta_map)}")
    plt.show()
    plt.close()
    plt.scatter(coords_ras[1, :], roi_betas)
    plt.xlabel("Posterior - Anterior")
    plt.ylabel(f"{op.basename(beta_map)}")
    plt.show()
    plt.close()
    # plot histogram of beta values within roi mask
    plt.scatter(coords_ras[2, :], roi_betas)
    plt.xlabel("Inferior - Superior")
    plt.ylabel(f"{op.basename(beta_map)}")
    plt.show()
    plt.close()

def roi_centers(big_roi_fn, subdivision_rois_fns, ref_vol_img):
    M = ref_vol_img.affine[:3, :3]
    abc = ref_vol_img.affine[:3, 3]
    big_roi = load_img(big_roi_fn)
    subdivision_rois = [load_img(f) for f in subdivision_rois_fns]
    big_coords = np.argwhere(big_roi.get_fdata()!=0)
    big_roi_center = M.dot(np.mean(big_coords, 0)) + abc
    big_roi_max_bounds = M.dot(np.max(big_coords, 0)) + abc
    big_roi_min_bounds = M.dot(np.min(big_coords, 0)) + abc
    big_roi_extent = big_roi_max_bounds-big_roi_min_bounds
    print(f"Big roi extends from {big_roi_min_bounds} to {big_roi_max_bounds}\nExtent is {big_roi_extent} and center is {big_roi_center}")
    fig, ax = plt.subplots(1)
    for fn,roi in zip(subdivision_rois_fns,subdivision_rois):
        coords = np.argwhere(roi.get_fdata()!=0)
        roi_center = M.dot(np.mean(coords, 0)) + abc
        roi_center_proportion = (big_roi_max_bounds - roi_center)/big_roi_extent
        ax.scatter(roi_center_proportion[0], 1-roi_center_proportion[2])
        ax.set_xlabel("Proportion of LGN extent (L-R)")
        ax.set_ylabel("Proportion of LGN extent (Ventral - Dorsal)")
        print(fn, roi_center, roi_center_proportion, sep='\n')
    plt.show()
    plt.close('all')

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
        print("ROI max and min coords", roi_max_bounds, roi_min_bounds)
        #for i in range(roi_min_bounds[2], roi_max_bounds[2]+1)
        #    [roi_min_bounds[0]-1, roi_min_bounds[1]-1, i]
        roi_extent = roi_max_bounds-roi_min_bounds+1
        print("ROI extent (total voxel span and max/min distance from center): ", roi_extent, roi_max_bounds-roi_center, roi_min_bounds-roi_center, sep='\n')
        print("ROI center in EPI and real-world coordinates: ", roi_center, M.dot(roi_center) + abc, sep='\n')
        print("****")

def assign_roi_percentile(roi, beta_map, cut_pct, ref_vol_img, which_hemi=None, roi_below_suffix='P', roi_above_suffix='M'):
    """This function takes an roi mask (nifti) and a map of values (originally betas for GLM contrasts but could also be pRF results etc).
    It looks at the values in the map within the ROI and identifies the specified (cut_pct) percentile.
    It then assigns the voxels to one of two regions based on if they're above or below this value.
    It also displays some graphs and stuff about this."""
    # first, figure out what the filenames of the new ROIs will be
    roi_stub = op.basename(roi).split('.')[0]
    roi_stub_parts = roi_stub.split('_')
    desc = [(i, x) for i, x in enumerate(roi_stub_parts) if 'desc-' in x]
    # desc[0][0] = index of description part in list of parts
    # desc[0][1] = actual desc-whatever
    roi_dir = op.dirname(roi)
    # desc-LLGN -> desc-LLGNM80 or whatever
    roi_below_name = f"{desc[0][1]}{roi_below_suffix}{cut_pct}"
    roi_above_name = f"{desc[0][1]}{roi_above_suffix}{cut_pct}"
    #print(roi_stub, roi_stub_parts, desc, roi_above_name, roi_below_name)
    roi_below_filename = f"{op.join(roi_dir, '_'.join([*roi_stub_parts[:desc[0][0]], roi_below_name, *roi_stub_parts[desc[0][0]+1:]]))}.nii.gz"
    roi_above_filename = f"{op.join(roi_dir, '_'.join([*roi_stub_parts[:desc[0][0]], roi_above_name, *roi_stub_parts[desc[0][0]+1:]]))}.nii.gz"

    # get coordinates of roi, calculate bounds
    coords = np.argwhere(load_img(roi).get_fdata()!=0)
    roi_max_bounds = np.max(coords, 0)
    roi_min_bounds = np.min(coords, 0)

    print(f"****\n****\nGiven the LGN mask \n{roi}\nwhich extends from {roi_max_bounds} to {roi_min_bounds}\nwill partition at {cut_pct}% and create\n{roi_below_filename}\n{roi_above_filename}", sep='\n')

    # mask the beta map with the roi mask
    beta_masker = NiftiMasker(mask_img=roi)
    roi_betas = beta_masker.fit_transform(beta_map)[0]

    roi_beta_min = np.min(roi_betas)
    roi_beta_max = np.max(roi_betas)

    # plot histogram of beta values within roi mask
    plt.hist(roi_betas, bins=16)
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
    below_mask = math_img(f"np.logical_and((img != 0), (img >= {-1*threshold}))", img=roi_nilearn_neg)
    n_vox_above = np.count_nonzero(above_mask.get_data())
    n_vox_below = np.count_nonzero(below_mask.get_data())

    # save files
    above_mask.to_filename(roi_above_filename)
    below_mask.to_filename(roi_below_filename)
    print(f"{roi_above_filename}: {n_vox_above} voxels\n{roi_below_filename}: {n_vox_below} voxels")
    # we should have assigned all voxels in the ROI mask to one of the two new ROIs
    assert(len(roi_betas)==n_vox_above+n_vox_below)
    
    # fix/change this to not assume M/P
    # also this could probably be refactored out into its own function that can be used elsewhere
    imgs_in_order = [load_img(roi_below_filename), load_img(roi_above_filename), load_img(beta_map)] #[load_img(roi_above_filename), load_img(roi_below_filename), beta_map]
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
    print(f"beta_masked: {beta_masked.shape}")

    # gets the coordinates of unmasked voxels
    xs, ys, zs = beta_masked.nonzero()

    roi_min_x = np.min(xs)
    roi_min_y = np.min(ys)
    roi_max_x = np.max(xs)
    roi_max_y = np.max(ys)
    roi_min_z = np.min(zs)
    roi_max_z = np.max(zs)
    roi_extent_y = roi_max_y - roi_min_y + 1
    roi_extent_z = roi_max_z - roi_min_z + 1

    # display first set of slices (z) and include a narrow colorbar axis
    gridspec = {'width_ratios': [1]*roi_extent_z + [0.1]}
    fig, ax = plt.subplots(ncols=roi_extent_z+1, figsize=(16,6), gridspec_kw=gridspec)
    for ri in range(roi_extent_z):
        # as of 2021-08-12, making this change to the orientation of the beta map to display correctly
        to_show = np.fliplr(beta_masked[roi_min_x-2:roi_max_x+2,roi_min_y-2:roi_max_y+2,roi_max_z-ri].transpose())
        #print(to_show.shape, np.count_nonzero(~to_show.mask))
        pos = ax[ri].imshow(to_show, origin="lower",
            cmap="coolwarm", clim=(roi_beta_min, roi_beta_max),
            norm=colors.TwoSlopeNorm(vmin=roi_beta_min, vcenter=threshold, vmax=roi_beta_max))
        ax[ri].set_title(f"Slice {roi_max_z-ri}")
        ax[ri].set_xlabel(f"Left to Right")
        ax[ri].set_ylabel(f"Posterior to Anterior")
        ax[ri].set_xticks([])
        ax[ri].set_yticks([])
    plt.colorbar(pos, cax=ax[ri+1])
    plt.show()
    plt.close()

    # display second set of slices with different orientation (y)
    fig, ax = plt.subplots(ncols=roi_extent_y, figsize=(16,6))
    for ri in range(roi_extent_y):
        # as of 2021-08-12, making this change to the orientation of the beta map to display correctly
        to_show = np.fliplr(beta_masked[roi_min_x-2:roi_max_x+2,roi_max_y-ri,roi_min_z-2:roi_max_z+2].transpose())
        #print(to_show.shape, np.count_nonzero(~to_show.mask))
        pos = ax[ri].imshow(to_show, origin="lower",
            cmap="coolwarm", clim=(roi_beta_min, roi_beta_max),
            norm=colors.TwoSlopeNorm(vmin=roi_beta_min, vcenter=threshold, vmax=roi_beta_max))
        ax[ri].set_title(f"Slice {roi_max_y-ri}")
        ax[ri].set_xlabel(f"Left to Right")
        ax[ri].set_ylabel(f"Inferior to Superior")
        ax[ri].set_xticks([])
        ax[ri].set_yticks([])
    plt.show()
    plt.close('all')
    return above_mask, below_mask, threshold


## Functions for dealing with timeseries and doing coherence analysis
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

def get_timeseries_from_file(bold, mask, TR, **kwargs):
    """
    Given a bold file and roi mask, return a nitime TimeSeries object
    """
    masker = NiftiMasker(mask_img=mask, t_r=TR, **kwargs)
    return masker, ts.TimeSeries(data=masker.fit_transform(bold).T, sampling_interval=TR)

def seed_coherence_timeseries(seed_ts, target_ts, f_ub, f_lb, method=dict(NFFT=32)):
    conn_analyzer = nta.SeedCoherenceAnalyzer(seed_ts, target_ts, method=method)
    freq_idx = np.where((conn_analyzer.frequencies > f_lb) * (conn_analyzer.frequencies < f_ub))[0]
    logger.debug(f"Seed timeseries has shape: {seed_ts.shape}\nLooking at freq bins centered on: {conn_analyzer.frequencies[freq_idx]}")
    # mean coherence across voxels in each freq bin
    mean_coh = np.mean(conn_analyzer.coherence, axis=0)
    # mean coherence across voxels in freq bins of interest
    mean_coh_bandpass = np.mean(mean_coh[freq_idx])
    # coherence in freq band of interest for each voxel
    coh_by_voxel = np.mean(conn_analyzer.coherence[:, freq_idx], axis=1)
    phase_by_voxel = np.mean(conn_analyzer.relative_phases[:, freq_idx], axis=1)

    # plots of interest
    fig, ax = plt.subplots(3, figsize=(10, 12))
    ax[0].set_title("Mean coherence with seed across voxels at different frequencies")
    if len(seed_ts.shape) > 1:
        ax[0].plot(conn_analyzer.frequencies, np.mean(mean_coh, axis=0))
    else:
        ax[0].plot(conn_analyzer.frequencies, mean_coh)
    ax[0].axvline(x=f_lb)
    ax[0].axvline(x=f_ub)
    ax[0].set_xlabel("Frequency (Hz)")
    ax[0].set_ylabel("Coherence")

    # ax[1].set_title("Coherence of each voxel with seed at different frequencies")
    # for i in range(conn_analyzer.coherence.shape[0]):
    #     ax[1].plot(conn_analyzer.frequencies, conn_analyzer.coherence[i, :])
    # ax[1].axvline(x=f_lb)
    # ax[1].axvline(x=f_ub)
    # ax[1].set_xlabel("Frequency (Hz)")
    # ax[1].set_ylabel("Coherence")

    ax[1].set_title(f"Histogram of voxel coherence values within band {f_lb} < f < {f_ub} (N={coh_by_voxel.shape[0]})")
    ax[1].hist(coh_by_voxel)
    ax[1].set_xlabel("Coherence")
    ax[1].set_ylabel("# voxels")

    ax[2].set_title(f"Histogram of voxel phase values within band {f_lb} < f < {f_ub} (N={coh_by_voxel.shape[0]})")
    ax[2].hist(phase_by_voxel)
    ax[2].set_xlabel("Relative phase")
    ax[2].set_ylabel("# voxels")

    plt.subplots_adjust(hspace=.3)
    plt.show()
    plt.close('all')
    logger.debug((f"seed_ts: {seed_ts.data.shape}\n"
        f"target_ts: {target_ts.data.shape}\n{conn_analyzer.coherence.shape}\n{conn_analyzer.relative_phases.shape},"
        f"{mean_coh.shape}, {mean_coh_bandpass.shape}, {coh_by_voxel.shape}"))
    return conn_analyzer, (coh_by_voxel, phase_by_voxel)

def seed_coherence_analysis(bold, mask, seed_roi, TR, f_ub, f_lb, mean_seed=True, method=dict(NFFT=32)):
    """
    Given a bold file, brainmask, and seed ROI mask, do a seed coherence analysis.
    """
    logger.debug(f"bold: {bold}\nmask: {mask}\nseed_roi: {seed_roi}")
    target_masker, target_ts = get_timeseries_from_file(bold, mask, TR, detrend=False, standardize=False, high_pass=f_lb, low_pass=f_ub)
    seed_masker, seed_ts = get_timeseries_from_file(bold, seed_roi, TR, detrend=False, standardize=False, high_pass=f_lb, low_pass=f_ub)

    if mean_seed:
        seed_ts = ts.TimeSeries(data=np.mean(seed_ts.data, axis=0), sampling_interval=TR)
        n_seeds = 1
    else:
        n_seeds = seed_ts.data.shape[0]

    conn_analyzer, (coh_by_voxel, phase_by_voxel) = seed_coherence_timeseries(seed_ts, target_ts, f_ub, f_lb, method)

    logger.debug("seed_coherence_analysis() about to return...")
    return conn_analyzer, target_masker, coh_by_voxel, phase_by_voxel


## Functions for pRF
def make_timeseries_for_prf(bolds):
    """Takes a list of 4d nifti filenames, averages, cuts extra timepoints"""
    for i, bold_file in enumerate(bolds):
        img = load_img(bold_file)
        data = get_data(img)
        print(data.shape)
        if i==0:
            all_data = np.empty((*data.shape, len(bolds)))
        all_data[:, :, :, :, i] = data
    mean_timeseries = np.average(all_data, -1)
    return nib.Nifti1Image(mean_timeseries[:, :, :, :138], img.affine)


## Functions manipulating NIFTI images and FreeSurfer surfaces
def threshold(what, by, at, out_dir, how='more'):
    """Threshold a given prf output (what) by another (by, usually rsq) at a specific value"""
    w = load_img(what)
    b = load_img(by)
    assert(w.shape == b.shape)
    if how=='more':
        mask = b.get_fdata() < at
    elif how=='less':
        mask = b.get_fdata() > at
    marray = np.ma.masked_array(w.get_fdata(), mask)
    out_data = np.ma.filled(marray, fill_value=0)
    print(f"Thresholding {what} by {by} at thresh {at:.2f}...\n", 
        mask.shape, out_data.shape, np.count_nonzero(mask), np.count_nonzero(out_data))
    out_img = nib.Nifti1Image(out_data, w.affine)
    parts_by = os.path.basename(by).split('_')
    desc_by = [p for p in parts_by if 'desc' in p][0].split('-')[1] # gets whatever is after desc-
    parts = os.path.basename(what).split('_')
    parts.insert(-1, f"thresh-{desc_by}-{at:.2f}")
    out_file = '_'.join(parts)
    print(desc_by, parts, out_file, sep='\n')
    nib.save(out_img, f"{out_dir}/{out_file}")

def prf_to_anat(sub, brain_file, in_file, func2brain, out_dir):
    # put space-anat in there, replacing space-* if it exists
    parts = os.path.basename(in_file).split('_')
    parts = [p if 'space-' not in p else 'space-anat' for p in parts]
    if 'space-anat' not in parts:
        parts.insert(-1, 'space-anat')
    desc = [p for p in parts if 'desc' in p][0]
    try:
        thresh = [p for p in parts if 'thresh' in p][0]
    except IndexError as inst:
        thresh = 'nothresh'
    outname = f"{desc}-{thresh}"
    print(parts, outname)
    out_file = '_'.join(parts)
    cmd = f"flirt -ref {brain_file} -out {out_dir}/{out_file} -in {in_file} -init {func2brain} -interp nearestneighbour -applyxfm"
    print('flirt cmd:\n', cmd)
    print('fsleyes anat cmd:\n', f"fsleyes {brain_file} {out_dir}/{out_file}")
    os.system(cmd)
    for hemi in ("lh", "rh"):
        cmd2 = f"mri_vol2surf --src {out_dir}/{out_file} --o {out_dir}/{hemi}.{outname}.mgz --hemi {hemi} --regheader {sub} --projfrac 0.5"
        print(cmd2)
        os.system(cmd2)

def freeview_prfs(sub, hemi, prf_dir):
    """View prfs on surface in freeview with good colormap"""
    overlays = sorted(glob.glob(f"{prf_dir}/?h.desc-*.mgz"))
    disp_overlays = [x for x in overlays if ('rho' in x or 'theta' in x) and f"{hemi}." in x]
    print('\n'.join(disp_overlays))
    hemi_surf = f"$SUBJECTS_DIR/{sub}/surf/{hemi}.inflated"
    freeview_cmd = f"freeview -f {hemi_surf}"
    for x in disp_overlays:
        x_color = 'overlay_custom=0.01,255,0,0,1.57,125,255,0,3.14,0,255,255,4.71,125,0,255,6.28,255,0,0' if 'theta' in x else 'overlay_color=colorwheel'
        freeview_cmd = freeview_cmd + f":overlay={x}:{x_color}"
    print(freeview_cmd)

def make_func_parc_mask(ribbon_nii, parc_codes, func_ref_vol_path, out_fn, xfm_path):
    # Load ribbon file, get voxels identified by parc_codes
    ribbon_img = load_img(ribbon_nii)
    ribbon_data = ribbon_img.get_fdata()
    cortex_mask = np.zeros_like(ribbon_data)
    print(np.count_nonzero(cortex_mask))
    for c in parc_codes:
        cortex_mask[ribbon_data==c] = 1
    print(np.count_nonzero(cortex_mask))
    # save these voxels as a binarized mask in the original space and resolution (T1)
    cortex_mask_img = nib.Nifti1Image(cortex_mask, ribbon_img.affine)
    out_fn_t1 = f"{op.dirname(out_fn)}/{change_bids_description(out_fn, 'space-T1w', 'space')}.nii.gz"
    nib.save(cortex_mask_img, out_fn_t1)
    cmd = f"flirt -ref {func_ref_vol_path} -in {out_fn_t1} -out {out_fn} -init {xfm_path} -applyxfm"
    print(cmd)
    os.system(cmd)

## Functions for dealing with BIDS filenames
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

def change_bids_description(this_epi, desc, slug='desc', pos_if_not_exist=-1):
    """Utility function that inserts or replaces a desc-{whatever} in a bids filename

    2021-09-13: slug param allows any part to be replaced, not just desc-<whatever>"""
    epi_name = os.path.basename(this_epi)
    epi_stub = epi_name.split('.')[0]
    epi_stub_parts = epi_stub.split('_')
    part = [(i, x) for i, x in enumerate(epi_stub_parts) if slug in x]
    if part:
        epi_stub_parts[part[0][0]] = desc
    else:
        epi_stub_parts.insert(pos_if_not_exist, desc)
    epi_stub_mcf = '_'.join(epi_stub_parts)
    return epi_stub_mcf

def get_bids_part(fn, part):
    bn = op.basename(fn)
    parts = bn.split('_')
    return [p for p in parts if part in p][0]

def epireg_func_to_anat(epi, t1, t1_brain, wmseg_nii, out, xfm, inverse):
    epireg_cmd = (f"epi_reg -v --epi={epi} --t1={t1} "
                  f"--t1brain={t1_brain} --wmseg={wmseg_nii} --out={out}")
    print(epireg_cmd, os.system(epireg_cmd))

    inverse_cmd = f"convert_xfm -omat {inverse} -inverse {out}.mat && cp {out}.mat {xfm}"
    print(inverse_cmd, os.system(inverse_cmd))

def convert_label_to_vol(label_fn, sub, freesurfer_dir, template_fn, reg_mat, hemi, output_name):
    if reg_mat == "identity":
        reg_option = "--identity"
    elif op.exists(reg_mat):
        reg_option = f"--reg {reg_mat}"
    else:
        raise ValueError("Must provide a valid registration matrix or 'identity'")
    os.putenv('SUBJECTS_DIR', f"{freesurfer_dir}")
    cmd = (f"mri_label2vol --label {label_fn} --subject {sub} --temp {template_fn} "
           f"{reg_option} --hemi {hemi} --proj frac 0 1 .1 --o {output_name} "
           f"&& fslswapdim {output_name} x z -y {output_name} ")
    logger.debug(cmd)
    logger.debug(f"exited with status {os.system(cmd)}")

def convert_labels(labels, sub, out_dir, template_fn, freesurfer_dir, reg_mat="identity", space="T1w"):
    for l in labels:
        parts = op.basename(l).split('.')
        if parts[0] in ('lh','rh'):
            hemi = parts[0]
            hemi_LR = hemi[0].upper()
        else:
            raise ValueError("hemi is neither lh nor rh!")
        roi_name = parts[1]
        # space should change depending on if reg_mat is "identity" (T1w)
        # or something else (a functional space, probably - need a label to be passed in)
        out_fn_stub = f"{out_dir}/sub-{sub}_desc-{hemi_LR}{roi_name}_space-{space}"
        out_fn = f"{out_fn_stub}_roi.nii.gz"
        logger.debug(f"{parts}, {hemi}, {roi_name}")
        convert_label_to_vol(l, sub, freesurfer_dir, template_fn, reg_mat, hemi, out_fn)