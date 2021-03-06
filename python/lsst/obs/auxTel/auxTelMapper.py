#
# LSST Data Management System
# Copyright 2016 LSST Corporation.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#
"""The AuxTel Mapper."""  # necessary to suppress D100 flake8 warning.

from __future__ import division, print_function

import os

import lsst.afw.image.utils as afwImageUtils
from lsst.obs.base import CameraMapper, MakeRawVisitInfo
import lsst.daf.persistence as dafPersist

from lsst.obs.auxTel import AuxTel

__all__ = ["AuxTelMapper"]


class AuxTelMakeRawVisitInfo(MakeRawVisitInfo):
    """functor to make a VisitInfo from the FITS header of a raw image."""

    def setArgDict(self, md, argDict):
        """Fill an argument dict with arguments for makeVisitInfo and pop associated metadata.

        Parameters
        ----------
        md : `lsst.daf.base.PropertyList or PropertySet`
            image metadata
        argDict : `dict`
            The argument dictionary used to construct the visit info, modified in place
        """
        super(AuxTelMakeRawVisitInfo, self).setArgDict(md, argDict)
        argDict["darkTime"] = self.popFloat(md, "DARKTIME")

        # Done setting argDict; check values now that all the header keywords have been consumed
        argDict["darkTime"] = self.getDarkTime(argDict)

    def getDateAvg(self, md, exposureTime):
        """Return date at the middle of the exposure.

        Parameters
        ----------
        md : `lsst.daf.base.PropertyList or PropertySet`
            image metadata
        exposureTime : `float`
            exposure time, measure in seconds
        """
        dateObs = self.popIsoDate(md, "DATE-OBS")
        return self.offsetDate(dateObs, 0.5*exposureTime)


def assemble_raw(dataId, componentInfo, cls):
    """Called by the butler to construct the composite type "raw".

    Note that we still need to define "_raw" and copy various fields over.

    Parameters
    ----------
    dataId : `lsst.daf.persistence.dataId.DataId`
        the data ID
    componentInfo : `dict`
        dict containing the components, as defined by the composite definition in the mapper policy
    cls : `object`
        unused

    Returns
    -------
    exposure : `lsst.afw.image.exposure.exposure`
        The assembled exposure
    """
    from lsst.ip.isr import AssembleCcdTask

    config = AssembleCcdTask.ConfigClass()
    config.doTrim = False

    assembleTask = AssembleCcdTask(config=config)

    ampExps = componentInfo['raw_amp'].obj
    if len(ampExps) == 0:
        raise RuntimeError("Unable to read raw_amps for %s" % dataId)

    ccd = ampExps[0].getDetector()      # the same (full, CCD-level) Detector is attached to all ampExps

    ampDict = {}
    for amp, ampExp in zip(ccd, ampExps):
        ampDict[amp.getName()] = ampExp

    exposure = assembleTask.assembleCcd(ampDict)

    md = componentInfo['raw_hdu'].obj
    exposure.setMetadata(md)
    #
    # We need to standardize, but have no legal way to call std_raw.  The butler should do this for us.
    #
    auxTelMapper = AuxTelMapper()
    exposure = auxTelMapper.std_raw(exposure, dataId)

    return exposure


class AuxTelMapper(CameraMapper):
    """The Mapper for the AuxTel."""

    packageName = 'obs_auxTel'
    MakeRawVisitInfoClass = AuxTelMakeRawVisitInfo

    def __init__(self, inputPolicy=None, **kwargs):
        """Initialization for the AuxTel Mapper."""
        policyFile = dafPersist.Policy.defaultPolicyFile(self.packageName, "auxTelMapper.yaml", "policy")
        policy = dafPersist.Policy(policyFile)

        CameraMapper.__init__(self, policy, os.path.dirname(policyFile), **kwargs)
        #
        # The composite objects don't seem to set these
        #
        for d in (self.mappings, self.exposures):
            d['raw'] = d['_raw']

        afwImageUtils.defineFilter('NONE', 0.0, alias=['no_filter', 'OPEN', 'empty'])
        afwImageUtils.defineFilter('275CutOn', 0.0, alias=[])
        afwImageUtils.defineFilter('550CutOn', 0.0, alias=[])
        afwImageUtils.defineFilter('green', 0.0, alias=[])
        afwImageUtils.defineFilter('blue', 0.0, alias=[])

    def _makeCamera(self, policy, repositoryDir):
        """Make a camera (instance of lsst.afw.cameraGeom.Camera) describing the camera geometry.

        Parameters
        ----------
        policy : anything - unused
            Unused

        repositoryDir : anything - unused
            Unused

        Returns
        -------
        camera : `lsst.afw.cameraGeom.Camera`
            The camera object
        """
        return AuxTel()

    def _extractDetectorName(self, dataId):
        """Extract the dector name from a dataId.

        Parameters
        ----------
        dataId : `lsst.daf.persistence.dataId.DataId`
            The data ID

        Returns
        -------
        detectorName : `int`
            The detector name
        """
        return dataId["ccd"]

    def _computeCcdExposureId(self, dataId):
        """Compute the 64-bit (long) identifier for a CCD exposure.

        Parameters
        ----------
        dataId : `lsst.daf.persistence.dataId.DataId`
            The data ID

        Returns
        -------
        CcdExposureId : `int`
            The CcdExposureId
        """
        visit = dataId['visit']
        return int(visit)

    def query_raw_amp(self, format, dataId):
        """Return a list of tuples of values of the fields specified in format, in order.

        The composite type "raw" doesn't provide e.g. query_raw, so we defined type _raw in the .paf file
        with the same template, and forward requests as necessary

        Parameters
        ----------
        format : iterable
            The desired set of keys
        dataId : `lsst.daf.persistence.dataId.DataId`
            The (possibly-incomplete) dataId

        Returns
        -------
        dataIds : `list` of values, or `list` of `tuples` of channel/value pairs
            Iterable of dataId values, or channel/value pairs
        """
        nChannel = 16                   # number of possible channels, 1..nChannel

        if "channel" in dataId:         # they specified a channel
            dataId = dataId.copy()
            channels = [dataId.pop('channel')]
        else:
            channels = range(1, nChannel + 1)  # we want all possible channels

        if "channel" in format:           # they asked for a channel, but we mustn't query for it
            format = list(format)
            channelIndex = format.index('channel')  # where channel values should go
            format.pop(channelIndex)
        else:
            channelIndex = None

        dids = []                       # returned list of dataIds
        for value in self.query_raw(format, dataId):
            if channelIndex is None:
                dids.append(value)
            else:
                for c in channels:
                    did = list(value)
                    did.insert(channelIndex, c)
                    dids.append(tuple(did))

        return dids

    def query_raw(self, *args, **kwargs):
        """Magic method that is called automatically if it exists.

        This code redirects the call to the right place, necessary because of leading underscore on _raw.
        """
        return self.query__raw(*args, **kwargs)

    def map_raw_md(self, *args, **kwargs):
        """Magic method that is called automatically if it exists.

        This code redirects the call to the right place, necessary because of leading underscore on _raw.
        """
        return self.map__raw_md(*args, **kwargs)

    def map_raw_filename(self, *args, **kwargs):
        """Magic method that is called automatically if it exists.

        This code redirects the call to the right place, necessary because of leading underscore on _raw.
        """
        return self.map__raw_filename(*args, **kwargs)

    def bypass_raw_filename(self, *args, **kwargs):
        """Magic method that is called automatically if it exists.

        This code redirects the call to the right place, necessary because of leading underscore on _raw.
        """
        return self.bypass__raw_filename(*args, **kwargs)

    def map_raw_visitInfo(self, *args, **kwargs):
        """Magic method that is called automatically if it exists.

        This code redirects the call to the right place, necessary because of leading underscore on _raw.
        """
        return self.map__raw_visitInfo(*args, **kwargs)

    def bypass_raw_visitInfo(self, datasetType, pythonType, location, dataId):
        """Work around for reading metadata from multi-HDU files.

        afwImage.readMetadata() doesn't honour [hdu] suffixes in filenames.

        Parameters
        ----------
        datasetType : anything, unused
            Unused
        pythonType : anything, unused
            Unused
        location : `lsst.daf.persistence.ButlerLocation`
            Butler location
        dataId : anything, unused
            Unused

        Returns
        -------
        visitInfo : `lsst.afw.image.visitInfo`
            The visitInfo
        """
        import re
        import lsst.afw.image as afwImage

        fileName = location.getLocationsWithRoot()[0]
        mat = re.search(r"\[(\d+)\]$", fileName)
        if mat:
            hdu = int(mat.group(1))
            md = afwImage.readMetadata(fileName, hdu=hdu)
        else:
            md = afwImage.readMetadata(fileName)  # or hdu = INT_MIN; -(1 << 31)

        return afwImage.VisitInfo(md)

    def std_raw_amp(self, item, dataId):
        """Amplifier-wise standardization of image-like objects.

        Parameters
        ----------
        item : image-like object; any of `lsst.afw.image.Exposure`, `lsst.afw.image.DecoratedImage`,
               `lsst.afw.image.Image`, or `lsst.afw.image.MaskedImage`
        dataId : `lsst.daf.persistence.dataId.DataId`
            The data ID

        Returns
        -------
        exp : `lsst.afw.image.Exposure`
            The standardized exposure
        """
        return self._standardizeExposure(self.exposures['raw_amp'], item, dataId,
                                         trimmed=False, setVisitInfo=False)
