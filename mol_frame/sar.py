#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
###
SAR
###

*Created on Thu Mar 28, 2019 by A. Pahl*

Tools for SAR analysis."""

import base64, pickle, time
from io import BytesIO as IO
import os.path as op
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier

from rdkit.Chem import AllChem as Chem
from rdkit.Chem import Draw
from rdkit.Chem.Draw import SimilarityMaps
from rdkit import DataStructs

try:
    Draw.DrawingOptions.atomLabelFontFace = "DejaVu Sans"
    Draw.DrawingOptions.atomLabelFontSize = 18
except KeyError:  # Font "DejaVu Sans" is not available
    pass

from mol_frame import mol_frame as mf, mol_images as mi

# from typing import List,

# tp, fp, tn, fn: true_pos, fals_pos, true_neg, false_neg
@dataclass
class Accuracy:
    num: int
    tp: float
    fp: float
    tn: float
    fn: float
    overall: float
    active: float
    inactive: float
    kappa: float

    def conf_matrix(self, numbers="absolute"):
        if "abs" in numbers:  # absolute numbers
            df = pd.DataFrame(
                {
                    "Real Active": [self.tp, self.fn, self.tp + self.fn],
                    "Real Inactive": [self.fp, self.tn, self.fp + self.tn],
                    "Sub Total": [self.tp + self.fp, self.fn + self.tn, self.num],
                },
                index=["Pred. Active", "Pred. Inactive", "Sub Total"],
            )
        else:  # relative numbers in percent
            df = pd.DataFrame(
                {
                    "Real Active": [
                        100 * self.tp / self.num,
                        100 * self.fn / self.num,
                        100 * self.tp / self.num + 100 * self.fn / self.num,
                    ],
                    "Real Inactive": [
                        100 * self.fp / self.num,
                        100 * self.tn / self.num,
                        100 * self.fp / self.num + 100 * self.tn / self.num,
                    ],
                    "Sub Total": [
                        100 * self.tp / self.num + 100 * self.fp / self.num,
                        100 * self.fn / self.num + 100 * self.tn / self.num,
                        100 * self.num / self.num,
                    ],
                },
                index=["Pred. Active", "Pred. Inactive", "Sub Total"],
            )
            df = df.round(1)
        return df


COL_WHITE = "#ffffff"
COL_GREEN = "#ccffcc"
COL_YELLOW = "#ffffcc"
COL_RED = "#ffcccc"


class SAR(object):
    """Container class for SAR analysis.
    Operates on a copy of the original MolFrame.
    All methods of the SAR class return copies,
    except for the `train` method."""

    def __init__(self, molf: mf.MolFrame = None):
        """
        Parameters:
            molf: MolFrame instance."""

        if molf is not None:
            self.molf = molf.copy()
        self.model = None

    def __str__(self):
        shape = self.molf.data.shape
        keys = list(self.molf.data.keys())
        return f"MolFrame  Rows: {shape[0]:6d}  Columns: {shape[1]:2d}   {keys}"

    def __repr__(self):
        return self.__str__()

    def __getitem__(self, key):
        res = self.molf[key]
        if isinstance(res, mf.MolFrame):
            result = self.new()
            result.molf = res
        else:
            result = res
        return result

    def __getattr__(self, name):
        """Try to call undefined methods on the underlying pandas DataFrame."""
        if hasattr(self.molf, name):

            def method(*args, **kwargs):
                res = getattr(self.molf, name)(*args, **kwargs)
                if isinstance(res, mf.MolFrame):
                    result = self.new()
                    result.molf = res
                else:
                    result = res
                return result

            return method
        else:
            raise AttributeError

    def new(self):
        result = SAR()
        if self.model is None:
            result.model = None
        else:
            result.model = deepcopy(self.model)
        return result

    def write(self, **kwargs):
        bn = kwargs.get("name", self.config["NAME"])
        self.molf.write_csv(f"{bn}.tsv")

    def copy(self):
        result = SAR(self.molf)
        result.model = self.model
        return result

    def to_csv(self, fn, sep="\t", index=False):
        self.molf.write_csv(fn, sep="\t", index=index)

    def analyze(self, act_class="AC_Real", pred_class="AC_Pred"):
        """Prints the ratio of succcessful predictions for the molecules which have `act_class` and `pred_class` properties."""
        mol_ctr = Counter()
        hit_ctr = Counter()
        for _, rec in self.molf.data.iterrows():
            if act_class in rec and pred_class in rec:
                mol_ctr[rec[act_class]] += 1
                if rec[act_class] != rec[pred_class]:
                    continue
                hit_ctr[rec[act_class]] += 1
        if len(mol_ctr) > 0:
            sum_mol_ctr = sum(mol_ctr.values())
            sum_hit_ctr = sum(hit_ctr.values())
            print(
                "Number of correctly predicted molecules: {} / {}    ({:.2f}%)".format(
                    sum_hit_ctr, sum_mol_ctr, 100 * sum_hit_ctr / sum_mol_ctr
                )
            )
            print("\nCorrectly predicted molecules per Activity Class:")
            for c in sorted(hit_ctr):
                print("  {}:  {:.2f}".format(c, 100 * hit_ctr[c] / mol_ctr[c]))
        else:
            print(
                "No molecules found with both {} and {}.".format(act_class, pred_class)
            )
        return hit_ctr, mol_ctr

    def save_model(self, fn="sar"):
        if self.model is None:
            print("No model available.")
            return
        save_model(self.model, fn)

    def load_model(self, fn="sar", force=False):
        if self.model is not None and not force:
            print("There is already a model available. Use `force=True` to override.")
            return
        if not fn.endswith(".model"):
            fn = fn + ".model"
        with open(fn, "rb") as f:
            self.model = pickle.load(f)
        print(
            "  > model loaded (last modified: {}).".format(
                time.strftime("%Y-%m-%d %H:%M", time.localtime(op.getmtime(fn)))
            )
        )

    def train(
        self,
        act_class="AC_Real",
        n_est=500,
        rnd_state=1123,
        show_progress=True,
        **kwargs,
    ):
        self.model = train(
            self.molf,
            act_class=act_class,
            n_est=n_est,
            rnd_state=rnd_state,
            show_progress=show_progress,
            **kwargs,
        )

    def predict(self, threshold=0.5):
        if self.model is None:
            raise LookupError("No suitable model found. Please run `train` first.")
        result = self.copy()
        result.molf = predict(self.molf, self.model, threshold=threshold)
        return result

    def add_sim_maps(self):
        """Adds the similarity maps as images to the MolFrame.
        Returns a copy."""

        result = self.copy()
        result.molf = add_sim_maps(self.molf, self.model)
        return result

    def accuracy(self):
        """Returns a namedtuple Accuracy(num, overall, active, inactive, kappa).
        kappa calculation from P. Czodrowski (https://link.springer.com/article/10.1007/s10822-014-9759-6)"""
        pred = self.molf.data[
            (self.molf.data["AC_Real"].notna()) & (self.molf.data["AC_Pred"].notna())
        ].copy()
        ctr_pred_act = len(pred[pred["AC_Pred"] == 1])
        ctr_pred_inact = len(pred[pred["AC_Pred"] == 0])
        # print(ctr_real_act, ctr_real_inact)
        true_pos = len(pred[(pred["AC_Real"] == 1) & (pred["AC_Pred"] == 1)])
        true_neg = len(pred[(pred["AC_Real"] == 0) & (pred["AC_Pred"] == 0)])
        false_pos = len(pred[(pred["AC_Real"] == 0) & (pred["AC_Pred"] == 1)])
        false_neg = len(pred[(pred["AC_Real"] == 1) & (pred["AC_Pred"] == 0)])
        ctr_num_pred = true_pos + false_pos + true_neg + false_neg
        # print(true_pos, true_neg, false_pos, false_neg)
        acc = (true_pos + true_neg) / ctr_num_pred
        # baseline = (
        #     (true_neg + false_pos)
        #     * (true_neg + false_neg)
        #     / (ctr_num_pred * ctr_num_pred)
        # ) + (
        #     (false_neg + true_pos)
        #     * (false_pos + true_pos)
        #     / (ctr_num_pred * ctr_num_pred)
        # )
        baseline = (
            np.matmul([true_neg, false_neg], [true_neg, false_pos])
            / (ctr_num_pred * ctr_num_pred)
        ) + (
            np.matmul([false_pos, true_pos], [false_neg, true_pos])
            / (ctr_num_pred * ctr_num_pred)
        )
        kappa = (acc - baseline) / (1 - baseline)
        result = Accuracy(
            num=ctr_num_pred,
            tp=true_pos,
            fp=false_pos,
            tn=true_neg,
            fn=false_neg,
            overall=acc,
            active=true_pos / ctr_pred_act,
            inactive=true_neg / ctr_pred_inact,
            kappa=kappa,
        )
        return result

    def write_grid(self, **kwargs):
        highlight = kwargs.pop("highlight", False)
        if not highlight:
            return self.molf.write_grid(**kwargs)
        rec_list = []
        for _, rec in self.molf.data.iterrows():
            if rec["Confidence"] == "High":
                rec[
                    "Prob"
                ] = f'<div style="background-color: {COL_GREEN};">{rec["Prob"]}</div>'
                rec[
                    "Confidence"
                ] = f'<div style="background-color: {COL_GREEN};">{rec["Confidence"]}</div>'
            elif rec["Confidence"] == "Medium":
                rec[
                    "Prob"
                ] = f'<div style="background-color: {COL_YELLOW};">{rec["Prob"]}</div>'
                rec[
                    "Confidence"
                ] = f'<div style="background-color: {COL_YELLOW};">{rec["Confidence"]}</div>'
            else:
                rec[
                    "Prob"
                ] = f'<div style="background-color: {COL_RED};">{rec["Prob"]}</div>'
                rec[
                    "Confidence"
                ] = f'<div style="background-color: {COL_RED};">{rec["Confidence"]}</div>'
            if rec["AC_Real"] == rec["AC_Pred"]:
                rec[
                    "AC_Real"
                ] = f'<div style="background-color: {COL_GREEN};">{rec["AC_Real"]}</div>'
                rec[
                    "AC_Pred"
                ] = f'<div style="background-color: {COL_GREEN};">{rec["AC_Pred"]}</div>'
            else:
                rec[
                    "AC_Real"
                ] = f'<div style="background-color: {COL_RED};">{rec["AC_Real"]}</div>'
                rec[
                    "AC_Pred"
                ] = f'<div style="background-color: {COL_RED};">{rec["AC_Pred"]}</div>'
            rec_list.append(rec)
        tmp = mf.MolFrame(pd.DataFrame(rec_list))
        return tmp.write_grid(truncate=100, **kwargs)


def read_csv(name: str) -> SAR:
    bn = name
    molf = mf.read_csv(bn)
    result = SAR(molf)
    return result


def train(
    molf: mf.MolFrame,
    act_class="AC_Real",
    n_est=500,
    rnd_state=1123,
    show_progress=True,
    **kwargs,
):
    """Returns the trained model.
    The kwargs are passed to sklearn' s RandomForestClassifier constructor."""
    fps = []
    act_classes = []
    molf.find_mol_col()
    if show_progress:
        print("  [TRAIN] calculating fingerprints")
    for _, rec in molf.data.iterrows():
        fps.append(
            Chem.GetMorganFingerprintAsBitVect(molf.mol_method(rec[molf.use_col]), 2)
        )
        act_classes.append(rec[act_class])
    np_fps = []
    if show_progress:
        print("  [TRAIN] calculating Numpy arrays")
    for fp in fps:
        arr = np.zeros((1,))
        DataStructs.ConvertToNumpyArray(fp, arr)
        np_fps.append(arr)

    # get a random forest classifiert with 100 trees
    if show_progress:
        print("  [TRAIN] training RandomForestClassifier")
    rf = RandomForestClassifier(n_estimators=n_est, random_state=rnd_state, **kwargs)
    rf.fit(np_fps, act_classes)
    if show_progress:
        print("  [TRAIN] done.")
    return rf


def predict(molf: mf.MolFrame, model, threshold=0.5):
    def _predict_mol(mol):
        """Returns the predicted class and the probabilities for a molecule.

        Parameters:
            model: Output from `train()`."""
        fp = np.zeros((1,))
        DataStructs.ConvertToNumpyArray(Chem.GetMorganFingerprintAsBitVect(mol, 2), fp)
        fp = fp.reshape(1, -1)  # this removes the deprecation warning
        # predict_class = model.predict(fp)
        predict_prob = model.predict_proba(fp)
        # return predict_class[0], predict_prob[0][1])
        proba = round(predict_prob[0][1], 2)
        if proba > threshold:
            return 1, proba
        else:
            return 0, proba

    def _predict(s):
        mol = molf.mol_method(s[molf.use_col])
        result = _predict_mol(mol)
        return result  # returns tuple

    molf.find_mol_col()
    result = molf.copy()
    result.data[["AC_Pred", "Prob"]] = result.data.apply(
        _predict, axis=1, result_type="expand"
    )
    result.data["AC_Pred"] = result.data["AC_Pred"].astype(int)
    result["Confidence"] = "Low"
    result["Confidence"].loc[
        (result["Prob"] < (0.8 * threshold)) | (result["Prob"] > (1.2 * threshold))
    ] = "Medium"
    result["Confidence"].loc[
        (result["Prob"] < (0.4 * threshold)) | (result["Prob"] > (1.6 * threshold))
    ] = "High"
    return result


def save_model(model, fn="sar"):
    if not fn.endswith(".model"):
        fn = fn + ".model"
    with open(fn, "wb") as f:
        pickle.dump(model, f)


def read_sdf(fn, model_name=None):
    sarf = SAR(mf.read_sdf(fn))
    if model_name is None:
        print("  * No model was loaded. Please provide a name to load.")
    else:
        try:
            sarf.load_model(model_name)
        except FileNotFoundError:
            print(
                "  * Model {} could not be found. No model was loaded".format(
                    model_name
                )
            )
    return sarf


def b64_fig(fig, dpi=72):
    img_file = IO()
    # print(fig.savefig.__doc__)
    # print([x for x in dir(fig) if x.startswith("set_")])
    # print(sorted(dir(fig)))
    # print(fig.get_edgecolor(), fig.get_facecolor())
    fig.savefig(img_file, dpi=dpi, format="PNG", bbox_inches="tight")
    img = mi.Image.open(img_file)
    img = mi.autocrop(img)
    img_file.close()
    img_file = IO()
    img.save(img_file, format="PNG")
    b64 = base64.b64encode(img_file.getvalue())
    b64 = b64.decode()
    img_file.close()
    return b64


def _get_proba(fp, predictionFunction):
    result = predictionFunction([fp])
    return result[0][1]


def add_sim_maps(molf: mf.MolFrame, model):
    """Adds the similarity maps as images to the MolFrame.
    Returns a copy."""

    def _map(x):
        mol = molf.mol_method(x)
        fig, _ = SimilarityMaps.GetSimilarityMapForModel(
            mol,
            SimilarityMaps.GetMorganFingerprint,
            lambda y: _get_proba(y, model.predict_proba),
            linewidths=0,
        )
        b64 = b64_fig(fig, dpi=72)
        img_src = '<img src="data:image/png;base64,{}" alt="Map" />'.format(b64)
        return img_src

    molf.find_mol_col()
    result = molf.copy()
    result.data["Map"] = result.data[molf.use_col].apply(_map)
    return result
