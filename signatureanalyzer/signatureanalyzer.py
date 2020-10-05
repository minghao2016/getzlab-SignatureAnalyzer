import sys
import argparse
import os
import pkg_resources
import pandas as pd
from typing import Union
import numpy as np
import matplotlib.pyplot as plt

from .utils import postprocess_msigs, get_nlogs_from_output, file_loader
from .utils import load_cosmic_signatures
from .utils import split_negatives
from .utils import assign_signature_weights_to_maf
from .utils import get96_from_1536

from .consensus import consensus_cluster

from .context import context1536, context78, context96, context_composite, context83

from .plotting import k_dist, consensus_matrix
from .plotting import signature_barplot, stacked_bar, signature_barplot_DBS, signature_barplot_ID, signature_barplot_composite, signature_barplot_sbs_id
from .plotting import marker_heatmap
from .plotting import cosine_similarity_plot

from .spectra import get_spectra_from_maf
from .bnmf import ardnmf

def run_maf(
    maf: Union[str, pd.DataFrame],
    outdir: str = '.',
    cosmic: str = 'cosmic2',
    hg_build: Union[str, None] = None,
    nruns: int = 10,
    verbose: bool = False,
    plot_results: bool = True,
    **nmf_kwargs
    ):
    """
    Args:
        * maf: input .maf file format
        * outdir: output directory to save files
        * cosmic: cosmic signature set to use
        * hg_build: human genome build for generating reference context
        * nruns: number of iterations for ARD-NMF
        * verbose: bool

    NMF_kwargs:
        * K0: starting number of latent components
        * objective: objective function for optimizaiton
        * max_iter: maximum number of iterations for algorithm
        * del_: n/a
        * tolerance: stop point for optimization
        * phi: dispersion parameter
        * a: shape parameter
        * b: shape parameter
        * prior_on_W: L1 or L2
        * prior_on_H: L1 or L2
        * report_freq: how often to print stats
        * active_thresh: threshold for a latent component's impact on
            signature if the latent factor is less than this, it does not contribute
        * cut_norm: min normalized value for mean signature
            (used in post-processing)
        * cut_diff: difference between mean signature and rest of signatures
            for marker selction
            (used in post-processing)
        * cuda_int: GPU to use. Defaults to 0. If "None" or if no GPU available,
            will perform decomposition using CPU.
    """
    try:
        [nmf_kwargs.pop(key) for key in ['input', 'type']]
    except:
        pass

    if outdir is not ".":
        print("   * Creating output dir at {}".format(outdir))
        os.makedirs(outdir, exist_ok=True)

    # Human Genome Build
    if hg_build is not None:
        print("   * Using {} build".format(hg_build.split("/")[-1].split('.2bit')[0]))

    # Cosmic Signatures
    cosmic_df, cosmic_index = load_cosmic_signatures(cosmic)

    composite = (cosmic in ['cosmic3_composite', 'cosmic3_composite96'])
    
    # Generate Spectra from Maf
    print("   * Loading spectra from {}".format(maf))
    maf, spectra = get_spectra_from_maf(
        pd.read_csv(maf, sep='\t'),
        hgfile=hg_build,
        cosmic=cosmic,
        composite=composite
    )

    print("   * Saving ARD-NMF outputs to {}".format(os.path.join(outdir,'nmf_output.h5')))
    store = pd.HDFStore(os.path.join(outdir,'nmf_output.h5'),'w')

    print("   * Running ARD-NMF...")
    for n_iter in range(nruns):
        store['X'] = spectra

        res = ardnmf(
            spectra,
            tag="\t{}/{}: ".format(n_iter,nruns-1),
            verbose=verbose,
            composite=composite,
            **nmf_kwargs
        )

        postprocess_msigs(res, cosmic_df, cosmic_index, cosmic)
        lam = pd.DataFrame(data=res["lam"], columns=["lam"])
        lam.index.name = "K0"

        store["run{}/H".format(n_iter)] = res["H"]
        store["run{}/W".format(n_iter)] = res["W"]
        store["run{}/lam".format(n_iter)] = lam
        store["run{}/Hraw".format(n_iter)] = res["Hraw"]
        store["run{}/Wraw".format(n_iter)] = res["Wraw"]
        store["run{}/markers".format(n_iter)] = res["markers"]
        store["run{}/signatures".format(n_iter)] = res["signatures"]
        store["run{}/log".format(n_iter)] = res["log"]
        store["run{}/cosine".format(n_iter)] = res["cosine"]
        if cosmic in ["cosmic3_1536", "cosmic3_composite", "cosmic3_composite96"]:
            store["run{}/cosine96".format(n_iter)] = res["cosine96"]
            store["run{}/Wraw96".format(n_iter)] = res["Wraw96"]
            store["run{}/W96".format(n_iter)] = res["W96"]

    store.close()

    # Select Best Result
    aggr = get_nlogs_from_output(os.path.join(outdir,'nmf_output.h5'))
    max_k = aggr.groupby("K").size().idxmax()
    max_k_iter = aggr[aggr['K']==max_k].shape[0]
    best_run = int(aggr[aggr['K']==max_k].obj.idxmin())
    print("   * Run {} had lowest objective with mode (n={:g}) K = {:g}.".format(best_run, max_k_iter, aggr.loc[best_run]['K']))

    store = pd.HDFStore(os.path.join(outdir,'nmf_output.h5'),'a')
    store["H"] = store["run{}/H".format(best_run)]
    store["W"] = store["run{}/W".format(best_run)]
    store["lam"] = store["run{}/lam".format(best_run)]
    store["Hraw"] = store["run{}/Hraw".format(best_run)]
    store["Wraw"] = store["run{}/Wraw".format(best_run)]
    store["markers"] = store["run{}/markers".format(best_run)]
    store["signatures"] = store["run{}/signatures".format(best_run)]
    store["log"] = store["run{}/log".format(best_run)]
    store["cosine"] = store["run{}/cosine".format(best_run)]
    store["aggr"] = aggr
    if cosmic in ["cosmic3_1536", "cosmic3_composite", "cosmic3_composite96"]:
        store["cosine96"] = store["run{}/cosine96".format(best_run)]
        store["Wraw96"] = store["run{}/Wraw96".format(best_run)]
        store["W96"] = store["run{}/W96".format(best_run)]
    store.close()

    H = pd.read_hdf(os.path.join(outdir, 'nmf_output.h5'), "H")
    W = pd.read_hdf(os.path.join(outdir, 'nmf_output.h5'), "W")

    weighted_maf = assign_signature_weights_to_maf(maf, W, H)
    weighted_maf.to_csv(os.path.join(outdir, 'signature_weighted_maf.tsv'), sep='\t', index=False)

    # Plots
    if plot_results:
        print("   * Saving report plots to {}".format(outdir))

        cosine = pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "cosine")

        if cosmic == 'cosmic3_DBS':
            _ = signature_barplot_DBS(W, contributions=np.sum(H))
        elif cosmic == 'cosmic3_ID':
            _ = signature_barplot_ID(W, contributions=np.sum(H))
        elif cosmic == 'cosmic3_1536':
            # Plot 96 Sanger cosine similarity
            _ = cosine_similarity_plot(pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "cosine96"))
            plt.savefig(os.path.join(outdir, "cosine_similarity_plot_96.pdf"), dpi=100, bbox_inches='tight')
            # Plot signature contributions for COSMIC signatures
            H96 = H.copy()
            W96 = pd.read_hdf(os.path.join(outdir, 'nmf_output.h5'), "W96")
            H96.columns = W96.columns
            _ = signature_barplot(W96, contributions=np.sum(H96))
            plt.savefig(os.path.join(outdir, "signature_contributions_COSMIC.pdf"),dpi=100,bbox_inches='tight')
            # Plot PCAWG Composite signature contributions
            _ = signature_barplot(get96_from_1536(W), contributions=np.sum(H))
        elif cosmic in ['cosmic3_composite', 'cosmic3_composite96']:
            H96 = H.copy()
            W96 = pd.read_hdf(os.path.join(outdir, 'nmf_output.h5'), "W96")
            H96.columns = W96.columns
            # Plot Sanger 96 SBS stacked
            _ = stacked_bar(H96, 'cosmic3')
            plt.savefig(os.path.join(outdir,'signature_stacked_barplot_cosmic.pdf'), dpi=100, bbox_inches='tight')
            # Plot 96 cosine similarity
            _ = cosine_similarity_plot(pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "cosine96"))
            plt.savefig(os.path.join(outdir, "cosine_similarity_plot_96.pdf"),dpi=100,bbox_inches='tight')
            # Plot signature contributions for COSMIC signatures
            _ = signature_barplot(W96, contributions=np.sum(H96))
            plt.savefig(os.path.join(outdir, "signature_contributions_COSMIC.pdf"),dpi=100,bbox_inches='tight')
            # Plot PCAWG Composite signature contributions
            if cosmic == 'cosmic3_composite':
                W_plot = pd.concat([get96_from_1536(W[W.index.isin(context1536)]),W[~W.index.isin(context1536)]])
            else:
                W_plot = W
            _ = signature_barplot_composite(W_plot, contributions=np.sum(H))
            
        else:
            _ = signature_barplot(W, contributions=np.sum(H))
            
        plt.savefig(os.path.join(outdir, "signature_contributions.pdf"), dpi=100, bbox_inches='tight')
        _ = stacked_bar(H, cosmic)
        plt.savefig(os.path.join(outdir, "signature_stacked_barplot.pdf"), dpi=100, bbox_inches='tight')
        _ = k_dist(np.array(aggr.K, dtype=int))
        plt.savefig(os.path.join(outdir, "k_dist.pdf"), dpi=100, bbox_inches='tight')
        _ = cosine_similarity_plot(cosine)
        plt.savefig(os.path.join(outdir, "cosine_similarity_plot.pdf"), dpi=100, bbox_inches='tight')

def run_spectra(
    spectra: Union[str, pd.DataFrame],
    outdir: str = '.',
    cosmic: str = 'cosmic2',
    nruns: int = 10,
    verbose: bool = False,
    plot_results: bool = True,
    **nmf_kwargs
    ):
    """
    Args:
        * spectra: filepath or pd.DataFrame of input spectra file (context x samples)
            NOTE: index should be context in the following format (1234): 3[1>2]4
        * outdir: output directory to save files
        * cosmic: cosmic signature set to use
        * nruns: number of iterations for ARD-NMF
        * verbose: bool

    NMF_kwargs:
        * K0: starting number of latent components
        * objective: objective function for optimizaiton
        * max_iter: maximum number of iterations for algorithm
        * del_: n/a
        * tolerance: stop point for optimization
        * phi: dispersion parameter
        * a: shape parameter
        * b: shape parameter
        * prior_on_W: L1 or L2
        * prior_on_H: L1 or L2
        * report_freq: how often to print stats
        * active_thresh: threshold for a latent component's impact on
            signature if the latent factor is less than this, it does not contribute
        * cut_norm: min normalized value for mean signature
            (used in post-processing)
        * cut_diff: difference between mean signature and rest of signatures
            for marker selction
            (used in post-processing)
        * cuda_int: GPU to use. Defaults to 0. If "None" or if no GPU available,
            will perform decomposition using CPU.
    """
    try:
        [nmf_kwargs.pop(key) for key in ['input', 'type', 'hg_build']]
    except:
        pass

    # Load spectra
    if isinstance(spectra, str):
        spectra = file_loader(spectra)

    if outdir is not ".":
        print("   * Creating output dir at {}".format(outdir))
        os.makedirs(outdir, exist_ok=True)

    # Cosmic Signatures
    cosmic_df, cosmic_index = load_cosmic_signatures(cosmic)

    composite = (cosmic in ['cosmic3_composite', 'cosmic3_composite96'])
    
    print("   * Saving ARD-NMF outputs to {}".format(os.path.join(outdir,'nmf_output.h5')))
    store = pd.HDFStore(os.path.join(outdir,'nmf_output.h5'),'w')

    print("   * Running ARD-NMF...")
    for n_iter in range(nruns):
        store['X'] = spectra

        res = ardnmf(
            spectra,
            tag="\t{}/{}: ".format(n_iter,nruns-1),
            verbose=verbose,
            composite=composite,
            **nmf_kwargs
        )

        postprocess_msigs(res, cosmic_df, cosmic_index, cosmic)
        lam = pd.DataFrame(data=res["lam"], columns=["lam"])
        lam.index.name = "K0"

        store["run{}/H".format(n_iter)] = res["H"]
        store["run{}/W".format(n_iter)] = res["W"]
        store["run{}/lam".format(n_iter)] = lam
        store["run{}/Hraw".format(n_iter)] = res["Hraw"]
        store["run{}/Wraw".format(n_iter)] = res["Wraw"]
        store["run{}/markers".format(n_iter)] = res["markers"]
        store["run{}/signatures".format(n_iter)] = res["signatures"]
        store["run{}/log".format(n_iter)] = res["log"]
        store["run{}/cosine".format(n_iter)] = res["cosine"]
        if cosmic in ["cosmic3_1536", "cosmic3_composite", "cosmic3_composite96", "cosmic3_sbs1536_id", "cosmic3_sbs96_id"]:
            store["run{}/cosine96".format(n_iter)] = res["cosine96"]
            store["run{}/Wraw96".format(n_iter)] = res["Wraw96"]
            store["run{}/W96".format(n_iter)] = res["W96"]

    store.close()

    # Select Best Result
    aggr = get_nlogs_from_output(os.path.join(outdir,'nmf_output.h5'))
    max_k = aggr.groupby("K").size().idxmax()
    max_k_iter = aggr[aggr['K']==max_k].shape[0]
    best_run = int(aggr[aggr['K']==max_k].obj.idxmin())
    print("   * Run {} had lowest objective with mode (n={:g}) K = {:g}.".format(best_run, max_k_iter, aggr.loc[best_run]['K']))

    store = pd.HDFStore(os.path.join(outdir,'nmf_output.h5'),'a')
    store["H"] = store["run{}/H".format(best_run)]
    store["W"] = store["run{}/W".format(best_run)]
    store["lam"] = store["run{}/lam".format(best_run)]
    store["Hraw"] = store["run{}/Hraw".format(best_run)]
    store["Wraw"] = store["run{}/Wraw".format(best_run)]
    store["markers"] = store["run{}/markers".format(best_run)]
    store["signatures"] = store["run{}/signatures".format(best_run)]
    store["log"] = store["run{}/log".format(best_run)]
    store["cosine"] = store["run{}/cosine".format(best_run)]
    store["aggr"] = aggr
    if cosmic in ["cosmic3_1536", "cosmic3_composite", "cosmic3_composite96", "cosmic3_sbs1536_id", "cosmic3_sbs96_id"]:
        store["cosine96"] = store["run{}/cosine96".format(best_run)]
        store["Wraw96"] = store["run{}/Wraw96".format(best_run)]
        store["W96"] = store["run{}/W96".format(best_run)]
    store.close()

    # Plots
    if plot_results:
        print("   * Saving report plots to {}".format(outdir))
        H = pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "H")
        W = pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "W")
        cosine = pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "cosine")

        if cosmic == 'cosmic3_DBS':
            _ = signature_barplot_DBS(W, contributions=np.sum(H))
        elif cosmic == 'cosmic3_ID':
            _ = signature_barplot_ID(W, contributions=np.sum(H))
        elif cosmic == 'cosmic3_1536':
            # Plot 96 Sanger cosine similarity
            _ = cosine_similarity_plot(pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "cosine96"))
            plt.savefig(os.path.join(outdir, "cosine_similarity_plot_96.pdf"), dpi=100, bbox_inches='tight')
            # Plot signature barplot with 96 Sanger SBS
            H96 = H.copy()
            W96 = pd.read_hdf(os.path.join(outdir, 'nmf_output.h5'), "W96")
            H96.columns = W96.columns
            _ = signature_barplot(W96, contributions=np.sum(H96))
        elif cosmic in ['cosmic3_composite','cosmic3_composite96']:
            H96 = H.copy()
            W96 = pd.read_hdf(os.path.join(outdir, 'nmf_output.h5'), "W96")
            H96.columns = W96.columns
            # Plot Sanger 96 SBS stacked
            _ = stacked_bar(H96, 'cosmic3')
            plt.savefig(os.path.join(outdir,'signature_stacked_barplot_cosmic.pdf'), dpi=100, bbox_inches='tight')
            # Plot 96 cosine similarity
            _ = cosine_similarity_plot(pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "cosine96"))
            plt.savefig(os.path.join(outdir, "cosine_similarity_plot_96.pdf"), dpi=100, bbox_inches='tight')
            # Plot signature contributions for COSMIC signatures
            _ = signature_barplot(W96, contributions=np.sum(H96))
            plt.savefig(os.path.join(outdir, "signature_contributions_COSMIC.pdf"), dpi=100,bbox_inches='tight')
            # Plot signature contributions for PCAWG SBS collapsed to 96
            ## Concatenate W96 with W DBS and ID rows
            if cosmic == 'cosmic3_composite':
                W_plot = pd.concat([get96_from_1536(W[W.index.isin(context1536)]),W[~W.index.isin(context1536)]])
            else:
                W_plot = W
            _ = signature_barplot_composite(W_plot, contributions=np.sum(H))
        elif cosmic in ['cosmic3_sbs1536_id', 'cosmic3_sbs96_id']:
            H96 = H.copy()
            W96 = pd.read_hdf(os.path.join(outdir, 'nmf_output.h5'), "W96")
            H96.columns = W96.columns
            # Plot Sanger 96 SBS stacked
            _ = stacked_bar(H96, 'cosmic3')
            plt.savefig(os.path.join(outdir,'signature_stacked_barplot_cosmic.pdf'), dpi=100, bbox_inches='tight')
            # Plot96 cosine similarity
            _ = cosine_similarity_plot(pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "cosine96"))
            plt.savefig(os.path.join(outdir, "cosine_similarity_plot_96.pdf"), dpi=100, bbox_inches='tight')
            # Plot signature contributions for COSMIC signatures
            _ = signature_barplot(W96, contributions=np.sum(H96))
            plt.savefig(os.path.join(outdir, "signature_contributions_COSMIC.pdf"), dpi=100,bbox_inches='tight')
            # Plot signature contributions for PCAWG SBS collapsed to 96 + ID
            ## Concatenate W96 with W  ID rows
            if cosmic == 'cosmic3_sbs1536_id':
                W_plot = pd.concat([get96_from_1536(W[W.index.isin(context1536)]),W[~W.index.isin(context1536)]])
            else:
                W_plot = W
            _ = signature_barplot_sbs_id(W_plot, contributions=np.sum(H))
        else:
            _ = signature_barplot(W, contributions=np.sum(H))

        plt.savefig(os.path.join(outdir, "signature_contributions.pdf"), dpi=100, bbox_inches='tight')
        _ = stacked_bar(H,cosmic)
        plt.savefig(os.path.join(outdir, "signature_stacked_barplot.pdf"), dpi=100, bbox_inches='tight')
        _ = k_dist(np.array(aggr.K, dtype=int))
        plt.savefig(os.path.join(outdir, "k_dist.pdf"), dpi=100, bbox_inches='tight')
        _ = cosine_similarity_plot(cosine)
        plt.savefig(os.path.join(outdir, "cosine_similarity_plot.pdf"), dpi=100, bbox_inches='tight')

def run_matrix(
    matrix: Union[str, pd.DataFrame],
    outdir: str = '.',
    nruns: int = 20,
    verbose: bool = False,
    plot_results: bool = True,
    **nmf_kwargs
    ):
    """
    Args:
em        * matrix: expression matrix; this should be normalized to accomodate
            Gaussian noise assumption (log2-norm) (n_features x n_samples)

            NOTE: recommended to filter out lowly expressed genes for RNA:
            *************** example filtering ***************
            tpm = tpm[
                (np.sum(tpm >= 0.1, 1) > tpm.shape[1]*0.2) &
                (np.sum(counts.iloc[:,1:] >= 6, 1) > tpm.shape[1]*0.2)
            ]
            *************************************************

            NOTE: reccomended to select a set of highly variable genes following
                this (~ 2000 - 7500 genes)

        * outdir: output directory to save files
        * cosmic: cosmic signature set to use
        * nruns: number of iterations for ARD-NMF
        * verbose: bool

    NMF_kwargs:
        * K0: starting number of latent components
        * objective: objective function for optimizaiton
        * max_iter: maximum number of iterations for algorithm
        * del_: n/a
        * tolerance: stop point for optimization
        * phi: dispersion parameter
        * a: shape parameter
        * b: shape parameter
        * prior_on_W: L1 or L2
        * prior_on_H: L1 or L2
        * report_freq: how often to print stats
        * active_thresh: threshold for a latent component's impact on
            signature if the latent factor is less than this, it does not contribute
        * cut_norm: min normalized value for mean signature
            (used in post-processing)
        * cut_diff: difference between mean signature and rest of signatures
            for marker selction
            (used in post-processing)
        * cuda_int: GPU to use. Defaults to 0. If "None" or if no GPU available,
            will perform decomposition using CPU.
    """
    try:
        [nmf_kwargs.pop(key) for key in ['input', 'type', 'hg_build', 'cosmic']]
    except:
        pass

    # Load matrix
    if isinstance(matrix, str):
        matrix = file_loader(matrix)

    # Check for negativity
    if min(matrix.min()) < 0:
        print("   * Negative values detecting, splitting vars m={} --> m={}".format(matrix.shape[0], matrix.shape[0]*2))
        matrix = split_negatives(matrix, axis=0)

    if outdir is not ".":
        print("   * Creating output dir at {}".format(outdir))
        os.makedirs(outdir, exist_ok=True)

    print("   * Saving ARD-NMF outputs to {}".format(os.path.join(outdir,'nmf_output.h5')))
    store = pd.HDFStore(os.path.join(outdir,'nmf_output.h5'),'w')

    print("   * Running ARD-NMF...")
    for n_iter in range(nruns):
        store['X'] = matrix

        res = ardnmf(
            matrix,
            tag="\t{}/{}: ".format(n_iter,nruns-1),
            verbose=verbose,
            **nmf_kwargs
        )

        lam = pd.DataFrame(data=res["lam"], columns=["lam"])
        lam.index.name = "K0"

        store["run{}/H".format(n_iter)] = res["H"]
        store["run{}/W".format(n_iter)] = res["W"]
        store["run{}/lam".format(n_iter)] = lam
        store["run{}/Hraw".format(n_iter)] = res["Hraw"]
        store["run{}/Wraw".format(n_iter)] = res["Wraw"]
        store["run{}/markers".format(n_iter)] = res["markers"]
        store["run{}/signatures".format(n_iter)] = res["signatures"]
        store["run{}/log".format(n_iter)] = res["log"]

    store.close()

    # Select Best Result
    aggr = get_nlogs_from_output(os.path.join(outdir,'nmf_output.h5'))
    max_k = aggr.groupby("K").size().idxmax()
    max_k_iter = aggr[aggr['K']==max_k].shape[0]
    best_run = int(aggr[aggr['K']==max_k].obj.idxmin())
    print("   * Run {} had lowest objective with mode (n={:g}) K = {:g}.".format(best_run, max_k_iter, aggr.loc[best_run]['K']))

    store = pd.HDFStore(os.path.join(outdir,'nmf_output.h5'),'a')
    store["H"] = store["run{}/H".format(best_run)]
    store["W"] = store["run{}/W".format(best_run)]
    store["lam"] = store["run{}/lam".format(best_run)]
    store["Hraw"] = store["run{}/Hraw".format(best_run)]
    store["Wraw"] = store["run{}/Wraw".format(best_run)]
    store["markers"] = store["run{}/markers".format(best_run)]
    store["signatures"] = store["run{}/signatures".format(best_run)]
    store["log"] = store["run{}/log".format(best_run)]
    store["aggr"] = aggr
    store.close()


    # Consensus Clustering
    print("   * Computing consensus matrix")
    cmatrix, _ = consensus_cluster(os.path.join(outdir, 'nmf_output.h5'))
    f,d = consensus_matrix(cmatrix, n_clusters=max_k_iter)

    cmatrix.to_csv(os.path.join(outdir, 'consensus_matrix.tsv'), sep='\t')
    d.to_csv(os.path.join(outdir, 'consensus_assign.tsv'), sep='\t')

    if plot_results: plt.savefig(os.path.join(outdir, 'consensus_matrix.pdf'), dpi=100, bbox_inches='tight')

    store = pd.HDFStore(os.path.join(outdir,'nmf_output.h5'),'a')
    store['consensus'] = d
    store.close()

    # Plots
    if plot_results:
        print("   * Saving report plots to {}".format(outdir))
        H = pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "H")
        X = pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "X")
        signatures = pd.read_hdf(os.path.join(outdir,'nmf_output.h5'), "signatures")

        _ = k_dist(np.array(aggr.K, dtype=int))
        plt.savefig(os.path.join(outdir, "k_dist.pdf"), dpi=100, bbox_inches='tight')

        _ = marker_heatmap(X, signatures, H.sort_values('max_id').max_id)
        plt.savefig(os.path.join(outdir, "marker_heatmap.pdf"), dpi=100, bbox_inches='tight')
