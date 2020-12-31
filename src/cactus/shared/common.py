#!/usr/bin/env python3
#Copyright (C) 2009-2011 by Benedict Paten (benedictpaten@gmail.com)
#
#Released under the MIT license, see LICENSE.txt
"""Wrapper functions for assisting in running the various programs of the cactus package.
"""

import os
import sys
import shutil
import subprocess
import logging
import pathlib
import pipes
import uuid
import json
import time
import signal
import hashlib
import tempfile
import math
import threading
import traceback
import errno
import shlex

try:
    import boto3
    import botocore
    has_s3 = True
except:
    has_s3 = False

from urllib.parse import urlparse
from datetime import datetime

from toil.lib.bioio import logger
from toil.lib.bioio import system
from toil.lib.bioio import getLogLevelString
from toil.common import Toil
from toil.job import Job
from toil.realtimeLogger import RealtimeLogger
from toil.lib.humanize import bytes2human

from sonLib.bioio import popenCatch

from cactus.shared.version import cactus_commit

_log = logging.getLogger(__name__)

subprocess._has_poll = False

def cactus_override_toil_options(options):
    """  Mess with some toil options to create useful defaults. """
    # Caching generally slows down the cactus workflow, plus some
    # methods like readGlobalFileStream don't support forced
    # reads directly from the job store rather than from cache.
    options.disableCaching = True
    # Job chaining breaks service termination timing, causing unused
    # databases to accumulate and waste memory for no reason.
    options.disableChaining = True
    # The default deadlockWait is currently 60 seconds. This can cause
    # issues if the database processes take a while to actually begin
    # after they're issued. Change it to at least an hour so that we
    # don't preemptively declare a deadlock.
    if options.deadlockWait is None or options.deadlockWait < 3600:
        options.deadlockWait = 3600
    if options.retryCount is None and options.batchSystem != 'singleMachine' :
        # If the user didn't specify a retryCount value, make it 5
        # instead of Toil's default (1).
        options.retryCount = 5

def makeURL(path_or_url):
    if urlparse(path_or_url).scheme == '':
        return "file://" + os.path.abspath(path_or_url)
    else:
        return path_or_url

def catFiles(filesToCat, catFile):
    """Cats a bunch of files into one file. Ensures a no more than maxCat files
    are concatenated at each step.
    """
    if len(filesToCat) == 0: #We must handle this case or the cat call will hang waiting for input
        open(catFile, 'w').close()
        return
    maxCat = 25
    system("cat %s > %s" % (" ".join(filesToCat[:maxCat]), catFile))
    filesToCat = filesToCat[maxCat:]
    while len(filesToCat) > 0:
        system("cat %s >> %s" % (" ".join(filesToCat[:maxCat]), catFile))
        filesToCat = filesToCat[maxCat:]

def cactusRootPath():
    """
    function for finding external location
    """
    import cactus
    i = os.path.abspath(cactus.__file__)
    return os.path.split(i)[0]

def getLogLevelString2(logLevelString):
    """Gets the log level string for the binary
    """
    if logLevelString == None:
        return getLogLevelString()
    return logLevelString

def getOptionalAttrib(node, attribName, typeFn=None, default=None):
    """Get an optional attrib, or default if not set or node is None
    """
    if node != None and attribName in node.attrib:
        if typeFn != None:
            if typeFn == bool:
                aname = node.attrib[attribName].lower()
                if aname == 'false':
                    return False
                elif aname == 'true':
                    return True
                else:
                    return bool(int(node.attrib[attribName]))
            return typeFn(node.attrib[attribName])
        return node.attrib[attribName]
    return default

def findRequiredNode(configNode, nodeName):
    """Retrieve an xml node, complain if it's not there."""
    nodes = configNode.findall(nodeName)
    if nodes == None:
        raise RuntimeError("Could not find any nodes with name %s in %s node" % (nodeName, configNode))
    assert len(nodes) == 1, "More than 1 node for %s in config XML" % nodeName
    return nodes[0]

#############################################
#############################################
#Following used to gather the names of flowers
#in problems
#############################################
#############################################

def readFlowerNames(flowerStrings):
    ret = []
    for line in flowerStrings.split("\n"):
        if line == '':
            continue
        flowersAndSizes = line[1:].split()
        numFlowers = flowersAndSizes[0]
        flowers = []
        sizes = []
        currentlyAFlower = True
        for token in flowersAndSizes[1:]:
            if token == 'a' or token == 'b':
                flowers += [token]
            elif currentlyAFlower:
                flowers += [token]
                currentlyAFlower = False
            else:
                sizes += [int(token)]
                currentlyAFlower = True
        assert len(sizes) == int(numFlowers)
        ret += [(bool(int(line[0])), " ".join([numFlowers] + flowers), sizes)]
    return ret

def runCactusGetFlowers(cactusDiskDatabaseString, flowerNames,
                        jobName=None, features=None, fileStore=None,
                        minSequenceSizeOfFlower=1,
                        maxSequenceSizeOfFlowerGrouping=-1,
                        maxSequenceSizeOfSecondaryFlowerGrouping=-1,
                        logLevel=None):
    """Gets a list of flowers attached to the given flower.
    """
    logLevel = getLogLevelString2(logLevel)
    flowerStrings = cactus_call(check_output=True, stdin_string=flowerNames,
                                parameters=["cactus_workflow_getFlowers", logLevel,
                                            cactusDiskDatabaseString,
                                            str(minSequenceSizeOfFlower),
                                            str(maxSequenceSizeOfFlowerGrouping),
                                            str(maxSequenceSizeOfSecondaryFlowerGrouping)],
                                job_name=jobName,
                                features=features,
                                fileStore=fileStore)

    l = readFlowerNames(flowerStrings)
    return l

def runCactusExtendFlowers(cactusDiskDatabaseString, flowerNames,
                        jobName=None, features=None, fileStore=None,
                        minSequenceSizeOfFlower=1,
                        maxSequenceSizeOfFlowerGrouping=-1,
                        maxSequenceSizeOfSecondaryFlowerGrouping=-1,
                        logLevel=None):
    """Extends the terminal groups in the cactus and returns the list
    of their child flowers with which to pass to core.
    The order of the flowers is by ascending depth first discovery time.
    """
    logLevel = getLogLevelString2(logLevel)
    flowerStrings = cactus_call(check_output=True, stdin_string=flowerNames,
                                parameters=["cactus_workflow_extendFlowers", logLevel,
                                            cactusDiskDatabaseString,
                                            str(minSequenceSizeOfFlower),
                                            str(maxSequenceSizeOfFlowerGrouping),
                                            str(maxSequenceSizeOfSecondaryFlowerGrouping)],
                                job_name=jobName,
                                features=features,
                                fileStore=fileStore)

    l = readFlowerNames(flowerStrings)
    return l

def encodeFlowerNames(flowerNames):
    if len(flowerNames) == 0:
        return "0"
    return "%i %s" % (len(flowerNames), " ".join([ str(flowerNames[0]) ] + [ str(flowerNames[i] - flowerNames[i-1]) for i in range(1, len(flowerNames)) ]))

def decodeFirstFlowerName(encodedFlowerNames):
    tokens = encodedFlowerNames.split()
    if int(tokens[0]) == 0:
        return None
    if tokens[1] == 'b':
        return int(tokens[2])
    return int(tokens[1])

def runCactusSplitFlowersBySecondaryGrouping(flowerNames):
    """Splits a list of flowers into smaller lists.
    """
    flowerNames = flowerNames.split()
    flowerGroups = []
    stack = []
    overlarge = False
    name = 0
    for i in flowerNames[1:]:
        if i != '':
            if i in ('a', 'b'):
                if len(stack) > 0:
                    flowerGroups.append((overlarge, encodeFlowerNames(stack))) #b indicates the stack is overlarge
                    stack = []
                overlarge = i == 'b'
            else:
                name = int(i) + name
                stack.append(name)
    if len(stack) > 0:
        flowerGroups.append((overlarge, encodeFlowerNames(stack)))
    return flowerGroups

#############################################
#############################################
#All the following provide command line wrappers
#for core programs in the cactus pipeline.
#############################################
#############################################

def runCactusSetup(cactusDiskDatabaseString, seqMap,
                   newickTreeString,
                   logLevel=None, outgroupEvents=None,
                   makeEventHeadersAlphaNumeric=False):
    logLevel = getLogLevelString2(logLevel)
    # We pass in the genome->sequence map as a series of paired arguments: [genome, faPath]*N.
    pairs = [[genome, faPath] for genome, faPath in list(seqMap.items())]
    args = [item for sublist in pairs for item in sublist]

    args += ["--speciesTree", newickTreeString, "--cactusDisk", cactusDiskDatabaseString,
            "--logLevel", logLevel]
    if makeEventHeadersAlphaNumeric:
        args += ["--makeEventHeadersAlphaNumeric"]
    if outgroupEvents:
        args += ["--outgroupEvents", " ".join(outgroupEvents)]
    masterMessages = cactus_call(check_output=True,
                                 parameters=["cactus_setup"] + args)

    logger.info("Ran cactus setup okay")
    return [ i for i in masterMessages.split("\n") if i != '' ]

def runConvertAlignmentsToInternalNames(cactusDiskString, alignmentsFile, outputFile, flowerName, isBedFile=False):
    args = [alignmentsFile, outputFile,
            "--cactusDisk", cactusDiskString]
    if isBedFile:
        args += ["--bed"]
    cactus_call(stdin_string=encodeFlowerNames((flowerName,)),
                parameters=["cactus_convertAlignmentsToInternalNames"] + args)

def runStripUniqueIDs(cactusDiskString):
    cactus_call(parameters=["cactus_stripUniqueIDs", "--cactusDisk", cactusDiskString])

def runCactusCaf(cactusDiskDatabaseString,
                 alignments,
                 secondaryAlignments=None,
                 flowerNames=encodeFlowerNames((0,)),
                 logLevel=None,
                 writeDebugFiles=False,
                 annealingRounds=None,
                 deannealingRounds=None,
                 trim=None,
                 minimumTreeCoverage=None,
                 blockTrim=None,
                 minimumBlockDegree=None,
                 minimumIngroupDegree=None,
                 minimumOutgroupDegree=None,
                 alignmentFilter=None,
                 lastzArguments=None,
                 minimumSequenceLengthForBlast=None,
                 maxAdjacencyComponentSizeRatio=None,
                 constraints=None,
                 minLengthForChromosome=None,
                 proportionOfUnalignedBasesForNewChromosome=None,
                 maximumMedianSequenceLengthBetweenLinkedEnds=None,
                 realign=False,
                 realignArguments=None,
                 phylogenyNumTrees=None,
                 phylogenyScoringMethod=None,
                 phylogenyRootingMethod=None,
                 phylogenyBreakpointScalingFactor=None,
                 phylogenySkipSingleCopyBlocks=False,
                 phylogenyMaxBaseDistance=None,
                 phylogenyMaxBlockDistance=None,
                 phylogenyDebugFile=None,
                 phylogenyKeepSingleDegreeBlocks=False,
                 phylogenyTreeBuildingMethod=None,
                 phylogenyCostPerDupPerBase=None,
                 phylogenyCostPerLossPerBase=None,
                 referenceEventHeader=None,
                 phylogenyDoSplitsWithSupportHigherThanThisAllAtOnce=None,
                 numTreeBuildingThreads=None,
                 doPhylogeny=False,
                 removeLargestBlock=None,
                 phylogenyNucleotideScalingFactor=None,
                 minimumBlockDegreeToCheckSupport=None,
                 minimumBlockHomologySupport=None,
                 removeRecoverableChains=None,
                 minimumNumberOfSpecies=None,
                 maxRecoverableChainsIterations=None,
                 maxRecoverableChainLength=None,
                 phylogenyHomologyUnitType=None,
                 phylogenyDistanceCorrectionMethod=None,
                 features=None,
                 jobName=None,
                 fileStore=None):
    logLevel = getLogLevelString2(logLevel)
    args = ["--logLevel", logLevel, "--alignments", alignments, "--cactusDisk", cactusDiskDatabaseString]
    if secondaryAlignments is not None:
        args += ["--secondaryAlignments", secondaryAlignments ]
    if annealingRounds is not None:
        args += ["--annealingRounds", annealingRounds]
    if deannealingRounds is not None:
        args += ["--deannealingRounds", deannealingRounds]
    if trim is not None:
        args += ["--trim", trim]
    if lastzArguments is not None:
        args += ["--lastzArguments", lastzArguments]
    if minimumTreeCoverage is not None:
        args += ["--minimumTreeCoverage", str(minimumTreeCoverage)]
    if blockTrim is not None:
        args += ["--blockTrim", str(blockTrim)]
    if minimumBlockDegree is not None:
        args += ["--minimumDegree", str(minimumBlockDegree)]
    if minimumSequenceLengthForBlast is not None:
        args += ["--minimumSequenceLengthForBlast", str(minimumSequenceLengthForBlast)]
    if minimumIngroupDegree is not None:
        args += ["--minimumIngroupDegree", str(minimumIngroupDegree)]
    if minimumOutgroupDegree is not None:
        args += ["--minimumOutgroupDegree", str(minimumOutgroupDegree)]
    if alignmentFilter is not None:
        args += ["--alignmentFilter", alignmentFilter]
    if maxAdjacencyComponentSizeRatio is not None:
        args += ["--maxAdjacencyComponentSizeRatio", str(maxAdjacencyComponentSizeRatio)]
    if constraints is not None:
        args += ["--constraints", constraints]
    if realign:
        args += ["--realign"]
    if realignArguments is not None:
        args += ["--realignArguments", realignArguments]
    if phylogenyNumTrees is not None:
        args += ["--phylogenyNumTrees", str(phylogenyNumTrees)]
    if phylogenyRootingMethod is not None:
        args += ["--phylogenyRootingMethod", phylogenyRootingMethod]
    if phylogenyScoringMethod is not None:
        args += ["--phylogenyScoringMethod", phylogenyScoringMethod]
    if phylogenyBreakpointScalingFactor is not None:
        args += ["--phylogenyBreakpointScalingFactor", str(phylogenyBreakpointScalingFactor)]
    if phylogenySkipSingleCopyBlocks:
        args += ["--phylogenySkipSingleCopyBlocks"]
    if phylogenyMaxBaseDistance is not None:
        args += ["--phylogenyMaxBaseDistance", str(phylogenyMaxBaseDistance)]
    if phylogenyMaxBlockDistance is not None:
        args += ["--phylogenyMaxBlockDistance", str(phylogenyMaxBlockDistance)]
    if phylogenyDebugFile is not None:
        args += ["--phylogenyDebugFile", phylogenyDebugFile]
    if phylogenyKeepSingleDegreeBlocks:
        args += ["--phylogenyKeepSingleDegreeBlocks"]
    if phylogenyTreeBuildingMethod is not None:
        args += ["--phylogenyTreeBuildingMethod", phylogenyTreeBuildingMethod]
    if phylogenyCostPerDupPerBase is not None:
        args += ["--phylogenyCostPerDupPerBase", str(phylogenyCostPerDupPerBase)]
    if phylogenyCostPerLossPerBase is not None:
        args += ["--phylogenyCostPerLossPerBase", str(phylogenyCostPerLossPerBase)]
    if referenceEventHeader is not None:
        args += ["--referenceEventHeader", referenceEventHeader]
    if phylogenyDoSplitsWithSupportHigherThanThisAllAtOnce is not None:
        args += ["--phylogenyDoSplitsWithSupportHigherThanThisAllAtOnce", str(phylogenyDoSplitsWithSupportHigherThanThisAllAtOnce)]
    if numTreeBuildingThreads is not None:
        args += ["--numTreeBuildingThreads", str(numTreeBuildingThreads)]
    if doPhylogeny:
        args += ["--phylogeny"]
    if minimumBlockDegreeToCheckSupport is not None:
        args += ["--minimumBlockDegreeToCheckSupport", str(minimumBlockDegreeToCheckSupport)]
    if minimumBlockHomologySupport is not None:
        args += ["--minimumBlockHomologySupport", str(minimumBlockHomologySupport)]
    if phylogenyNucleotideScalingFactor is not None:
        args += ["--phylogenyNucleotideScalingFactor", str(phylogenyNucleotideScalingFactor)]
    if removeRecoverableChains is not None:
        args += ["--removeRecoverableChains", removeRecoverableChains]
    if minimumNumberOfSpecies is not None:
        args += ["--minimumNumberOfSpecies", str(minimumNumberOfSpecies)]
    if maxRecoverableChainsIterations is not None:
        args += ["--maxRecoverableChainsIterations", str(maxRecoverableChainsIterations)]
    if maxRecoverableChainLength is not None:
        args += ["--maxRecoverableChainLength", str(maxRecoverableChainLength)]
    if phylogenyHomologyUnitType is not None:
        args += ["--phylogenyHomologyUnitType", phylogenyHomologyUnitType]
    if phylogenyDistanceCorrectionMethod is not None:
        args += ["--phylogenyDistanceCorrectionMethod", phylogenyDistanceCorrectionMethod]
    if minLengthForChromosome is not None:
        args += ["--minLengthForChromosome", str(minLengthForChromosome)]
    if proportionOfUnalignedBasesForNewChromosome is not None:
        args += ["--proportionOfUnalignedBasesForNewChromosome", str(proportionOfUnalignedBasesForNewChromosome)]
    if maximumMedianSequenceLengthBetweenLinkedEnds is not None:
        args += ["--maximumMedianSequenceLengthBetweenLinkedEnds", str(maximumMedianSequenceLengthBetweenLinkedEnds)]

    masterMessages = cactus_call(stdin_string=flowerNames, check_output=True,
                                 parameters=["cactus_caf"] + args,
                                 features=features, job_name=jobName, fileStore=fileStore)
    logger.info("Ran cactus_caf okay")
    return [ i for i in masterMessages.split("\n") if i != '' ]

def runCactusPhylogeny(cactusDiskDatabaseString,
                       flowerNames=encodeFlowerNames((0,)),
                       logLevel=None):
    logLevel = getLogLevelString2(logLevel)
    cactus_call(stdin_string=flowerNames,
                parameters=["cactus_phylogeny",
                            "--cactusDisk", cactusDiskDatabaseString,
                            "--logLevel", logLevel])
    logger.info("Ran cactus_phylogeny okay")

def runCactusAdjacencies(cactusDiskDatabaseString, flowerNames=encodeFlowerNames((0,)), logLevel=None):
    logLevel = getLogLevelString2(logLevel)
    cactus_call(stdin_string=flowerNames,
                parameters=["cactus_fillAdjacencies",
                            "--cactusDisk", cactusDiskDatabaseString,
                            "--logLevel", logLevel])
    logger.info("Ran cactus_fillAdjacencies OK")

def runCactusConvertAlignmentToCactus(cactusDiskDatabaseString, constraintsFile, newConstraintsFile, logLevel=None):
    """Takes a cigar file and makes an equivalent cigar file using the internal coordinate system format of cactus.
    """
    logLevel = getLogLevelString2(logLevel)
    cactus_call(parameters=["cactus_workflow_convertAlignmentCoordinates",
                            logLevel, cactusDiskDatabaseString,
                            constraintsFile, newConstraintsFile])

def runCactusFlowerStats(cactusDiskDatabaseString, flowerName, logLevel=None):
    """Prints stats for the given flower
    """
    logLevel = getLogLevelString2(logLevel)
    flowerStatsString = cactus_call(check_output=True,
                                    parameters=["cactus_workflow_flowerStats",
                                                logLevel, cactusDiskDatabaseString, str(flowerName)])
    return flowerStatsString

def runCactusMakeNormal(cactusDiskDatabaseString, flowerNames, maxNumberOfChains=0, logLevel=None):
    """Makes the given flowers normal (see normalisation for the various phases)
    """
    logLevel = getLogLevelString2(logLevel)
    cactus_call(stdin_string=flowerNames,
                parameters=["cactus_normalisation",
                            "--cactusDisk", cactusDiskDatabaseString,
                            "--maxNumberOfChains", str(maxNumberOfChains),
                            "--logLevel", logLevel])

def runCactusBar(cactusDiskDatabaseString, flowerNames, logLevel=None,
                 spanningTrees=None, maximumLength=None,
                 gapGamma=None,
                 matchGamma=None,
                 splitMatrixBiggerThanThis=None,
                 anchorMatrixBiggerThanThis=None,
                 repeatMaskMatrixBiggerThanThis=None,
                 diagonalExpansion=None,
                 constraintDiagonalTrim=None,
                 minimumBlockDegree=None,
                 minimumIngroupDegree=None,
                 minimumOutgroupDegree=None,
                 alignAmbiguityCharacters=False,
                 pruneOutStubAlignments=False,
                 useProgressiveMerging=False,
                 calculateWhichEndsToComputeSeparately=False,
                 largeEndSize=None,
                 endAlignmentsToPrecomputeOutputFile=None,
                 precomputedAlignments=None,
                 ingroupCoverageFile=None,
                 minimumSizeToRescue=None,
                 minimumCoverageToRescue=None,
                 minimumNumberOfSpecies=None,
                 partialOrderAlignment=None,
                 partialOrderAlignmentWindow=None,
                 partialOrderAlignmentMaskFilter=None,
                 partialOrderAlignmentBandConstant=None,
                 partialOrderAlignmentBandFraction=None,
                 jobName=None,
                 fileStore=None,
                 features=None):
    """Runs cactus base aligner."""
    logLevel = getLogLevelString2(logLevel)
    args = ["--logLevel", logLevel, "--cactusDisk", cactusDiskDatabaseString]
    if maximumLength is not None:
        args += ["--maximumLength", str(maximumLength)]
    if spanningTrees is not None:
        args += ["--spanningTrees", str(spanningTrees)]
    if gapGamma is not None:
        args += ["--gapGamma", str(gapGamma)]
    if matchGamma is not None:
        args += ["--matchGamma", str(matchGamma)]
    if splitMatrixBiggerThanThis is not None:
        args += ["--splitMatrixBiggerThanThis", str(splitMatrixBiggerThanThis)]
    if anchorMatrixBiggerThanThis is not None:
        args += ["--anchorMatrixBiggerThanThis", str(anchorMatrixBiggerThanThis)]
    if repeatMaskMatrixBiggerThanThis is not None:
        args += ["--repeatMaskMatrixBiggerThanThis", str(repeatMaskMatrixBiggerThanThis)]
    if diagonalExpansion is not None:
        args += ["--diagonalExpansion", str(diagonalExpansion)]
    if constraintDiagonalTrim is not None:
        args += ["--constraintDiagonalTrim", str(constraintDiagonalTrim)]
    if minimumBlockDegree is not None:
        args += ["--minimumDegree", str(minimumBlockDegree)]
    if minimumIngroupDegree is not None:
        args += ["--minimumIngroupDegree", str(minimumIngroupDegree)]
    if minimumOutgroupDegree is not None:
        args += ["--minimumOutgroupDegree", str(minimumOutgroupDegree)]
    if pruneOutStubAlignments:
        args += ["--pruneOutStubAlignments"]
    if alignAmbiguityCharacters:
        args += ["--alignAmbiguityCharacters"]
    if useProgressiveMerging:
        args += ["--useProgressiveMerging"]
    if calculateWhichEndsToComputeSeparately:
        args += ["--calculateWhichEndsToComputeSeparately"]
    if largeEndSize is not None:
        args += ["--largeEndSize", str(largeEndSize)]
    if endAlignmentsToPrecomputeOutputFile is not None:
        endAlignmentsToPrecomputeOutputFile = os.path.basename(endAlignmentsToPrecomputeOutputFile)
        args += ["--endAlignmentsToPrecomputeOutputFile", endAlignmentsToPrecomputeOutputFile]
    if precomputedAlignments is not None:
        precomputedAlignments = list(map(os.path.basename, precomputedAlignments))
        precomputedAlignments = " ".join(precomputedAlignments)
        args += ["--precomputedAlignments", precomputedAlignments]
    if ingroupCoverageFile is not None:
        args += ["--ingroupCoverageFile", ingroupCoverageFile]
    if minimumSizeToRescue is not None:
        args += ["--minimumSizeToRescue", str(minimumSizeToRescue)]
    if minimumCoverageToRescue is not None:
        args += ["--minimumCoverageToRescue", str(minimumCoverageToRescue)]
    if minimumNumberOfSpecies is not None:
        args += ["--minimumNumberOfSpecies", str(minimumNumberOfSpecies)]
    if partialOrderAlignment is True:
        assert partialOrderAlignmentWindow is not None and int(partialOrderAlignmentWindow) > 1
        args += ["--partialOrderAlignmentWindow", str(partialOrderAlignmentWindow)]
    if partialOrderAlignmentMaskFilter is not None and partialOrderAlignmentMaskFilter >= 0:
        args += ["--maskFilter", str(partialOrderAlignmentMaskFilter)]
    if partialOrderAlignmentBandConstant:
        args += ["--partialOrderAlignmentBandConstant", str(partialOrderAlignmentBandConstant)]
    if partialOrderAlignmentBandFraction:
        args += ["--partialOrderAlignmentBandFraction", str(partialOrderAlignmentBandFraction)]
        
    masterMessages = cactus_call(stdin_string=flowerNames, check_output=True,
                                 parameters=["cactus_bar"] + args,
                                 job_name=jobName, fileStore=fileStore, features=features)

    logger.info("Ran cactus_bar okay")
    return [ i for i in masterMessages.split("\n") if i != '' ]

def runCactusSecondaryDatabase(secondaryDatabaseString, create=True):
    cactus_call(parameters=["cactus_secondaryDatabase",
                secondaryDatabaseString, create])

def runCactusReference(cactusDiskDatabaseString, flowerNames, logLevel=None,
                       jobName=None, features=None, fileStore=None,
                       matchingAlgorithm=None,
                       referenceEventString=None,
                       permutations=None,
                       useSimulatedAnnealing=False,
                       theta=None,
                       phi=None,
                       maxWalkForCalculatingZ=None,
                       ignoreUnalignedGaps=False,
                       wiggle=None,
                       numberOfNs=None,
                       minNumberOfSequencesToSupportAdjacency=None,
                       makeScaffolds=False):
    """Runs cactus reference."""
    logLevel = getLogLevelString2(logLevel)
    args = ["--logLevel", logLevel, "--cactusDisk", cactusDiskDatabaseString]
    if matchingAlgorithm is not None:
        args += ["--matchingAlgorithm", matchingAlgorithm]
    if referenceEventString is not None:
        args += ["--referenceEventString", referenceEventString]
    if permutations is not None:
        args += ["--permutations", str(permutations)]
    if useSimulatedAnnealing:
        args += ["--useSimulatedAnnealing"]
    if theta is not None:
        args += ["--theta", str(theta)]
    if phi is not None:
        args += ["--phi", str(phi)]
    if maxWalkForCalculatingZ is not None:
        args += ["--maxWalkForCalculatingZ", str(maxWalkForCalculatingZ)]
    if ignoreUnalignedGaps:
        args += ["--ignoreUnalignedGaps"]
    if wiggle is not None:
        args += ["--wiggle", str(wiggle)]
    if numberOfNs is not None:
        args += ["--numberOfNs", str(numberOfNs)]
    if minNumberOfSequencesToSupportAdjacency is not None:
        args += ["--minNumberOfSequencesToSupportAdjacency", str(minNumberOfSequencesToSupportAdjacency)]
    if makeScaffolds:
        args += ["--makeScaffolds"]

    masterMessages = cactus_call(stdin_string=flowerNames, check_output=True,
                                 parameters=["cactus_reference"] + args,
                                 job_name=jobName,
                                 features=features,
                                 fileStore=fileStore)
    logger.info("Ran cactus_reference okay")
    return [ i for i in masterMessages.split("\n") if i != '' ]

def runCactusAddReferenceCoordinates(cactusDiskDatabaseString, flowerNames,
                                     jobName=None, fileStore=None, features=None,
                                     logLevel=None, referenceEventString=None,
                                     outgroupEventString=None, secondaryDatabaseString=None,
                                     bottomUpPhase=False):
    logLevel = getLogLevelString2(logLevel)
    args = ["--logLevel", logLevel, "--cactusDisk", cactusDiskDatabaseString]
    if bottomUpPhase:
        args += ["--bottomUpPhase"]
    if referenceEventString is not None:
        args += ["--referenceEventString", referenceEventString]
    if outgroupEventString is not None:
        args += ["--outgroupEventString", outgroupEventString]
    if secondaryDatabaseString is not None:
        args += ["--secondaryDisk", secondaryDatabaseString]
    cactus_call(stdin_string=flowerNames,
                parameters=["cactus_addReferenceCoordinates"] + args,
                job_name=jobName,
                features=features,
                fileStore=fileStore)

def runCactusCheck(cactusDiskDatabaseString,
                   flowerNames=encodeFlowerNames((0,)),
                   logLevel=None,
                   recursive=False,
                   checkNormalised=False):
    logLevel = getLogLevelString2(logLevel)
    args = ["--cactusDisk", cactusDiskDatabaseString, "--logLevel", logLevel]
    if recursive:
        args += ["--recursive"]
    if checkNormalised:
        args += ["--checkNormalised"]
    cactus_call(stdin_string=flowerNames,
                parameters=["cactus_check"] + args)
    logger.info("Ran cactus check")

def _fn(toilDir,
      logLevel=None, retryCount=0,
      batchSystem="single_machine",
      rescueJobFrequency=None,
      buildAvgs=False,
      buildHal=False,
      buildFasta=False,
      toilStats=False,
      maxThreads=None,
      maxCpus=None,
      defaultMemory=None,
      logFile=None):
    logLevel = getLogLevelString2(logLevel)
    args = [toilDir, "--logLevel", logLevel]
    if buildAvgs:
        args += ["--buildAvgs"]
    if buildHal:
        args += ["--buildHal"]
    if buildFasta:
        args += ["--buildFasta"]
    #Jobtree args
    if batchSystem is not None:
        args += ["--batchSystem", batchSystem]
    if retryCount is not None:
        args += ["--retryCount", str(retryCount)]
    if rescueJobFrequency is not None:
        args += ["--rescueJobFrequency", str(rescueJobFrequency)]
    if toilStats:
        args += ["--stats"]
    if maxThreads is not None:
        args += ["--maxThreads", str(maxThreads)]
    if maxCpus is not None:
        args += ["--maxCpus", str(maxCpus)]
    if defaultMemory is not None:
        args += ["--defaultMemory", str(defaultMemory)]
    if logFile is not None:
        args += ["--logFile", logFile]
    return args

def runCactusWorkflow(experimentFile,
                      toilDir,
                      logLevel=None, retryCount=0,
                      batchSystem="single_machine",
                      rescueJobFrequency=None,
                      skipAlignments=False,
                      buildAvgs=False,
                      buildHal=False,
                      buildFasta=False,
                      toilStats=False,
                      maxThreads=None,
                      maxCpus=None,
                      defaultMemory=None,
                      logFile=None,
                      intermediateResultsUrl=None,
                      extraToilArgumentsString=""):
    args = ["--experiment", experimentFile] + _fn(toilDir,
                      logLevel, retryCount, batchSystem, rescueJobFrequency,
                      buildAvgs, buildHal, buildFasta, toilStats, maxThreads, maxCpus, defaultMemory, logFile)
    if intermediateResultsUrl is not None:
        args += ["--intermediateResultsUrl", intermediateResultsUrl]

    import cactus.pipeline.cactus_workflow as cactus_workflow
    cactus_workflow.runCactusWorkflow(args)
    logger.info("Ran the cactus workflow okay")

def runCactusProgressive(seqFile,
                         configFile,
                         toilDir,
                         logLevel=None, retryCount=0,
                         batchSystem="single_machine",
                         rescueJobFrequency=None,
                         skipAlignments=False,
                         buildHal=True,
                         buildAvgs=False,
                         toilStats=False,
                         maxCpus=None):
    opts = Job.Runner.getDefaultOptions(toilDir)
    opts.batchSystem = batchSystem if batchSystem is not None else opts.batchSystem
    opts.logLevel = logLevel if logLevel is not None else opts.logLevel
    opts.maxCores = maxCpus if maxCpus is not None else opts.maxCores
    # Used for tests
    opts.scale = 0.1
    opts.retryCount = retryCount if retryCount is not None else opts.retryCount
    # This *shouldn't* be necessary, but it looks like the toil
    # deadlock-detection still has issues.
    opts.deadlockWait = 3600

    opts.buildHal = buildHal
    opts.buildAvgs = buildAvgs
    opts.buildFasta = True
    if toilStats:
        opts.stats = True
    opts.seqFile = seqFile
    opts.configFile = configFile
    opts.database = 'kyoto_tycoon'
    opts.root = None
    opts.outputHal = '/dev/null'
    opts.intermediateResultsUrl = None
    from cactus.progressive.cactus_progressive import runCactusProgressive as runRealCactusProgressive
    runRealCactusProgressive(opts)

def runCactusHalGenerator(cactusDiskDatabaseString,
                          secondaryDatabaseString,
                          flowerNames,
                          referenceEventString,
                          outputFile=None,
                          showOnlySubstitutionsWithRespectToReference=False,
                          logLevel=None,
                          jobName=None,
                          features=None,
                          fileStore=None):
    logLevel = getLogLevelString2(logLevel)
    if outputFile is not None:
        outputFile = os.path.basename(outputFile)
    args = ["--logLevel", logLevel, "--cactusDisk", cactusDiskDatabaseString,
            "--secondaryDisk", secondaryDatabaseString]
    if referenceEventString is not None:
        args += ["--referenceEventString", referenceEventString]
    if outputFile is not None:
        args += ["--outputFile", outputFile]
    if showOnlySubstitutionsWithRespectToReference:
        args += ["--showOnlySubstitutionsWithRespectToReference"]
    cactus_call(stdin_string=flowerNames,
                parameters=["cactus_halGenerator"] + args,
                job_name=jobName, features=features, fileStore=fileStore)

def runCactusFastaGenerator(cactusDiskDatabaseString,
                            flowerName,
                            outputFile,
                            referenceEventString,
                            logLevel=None):
    logLevel = getLogLevelString2(logLevel)
    cactus_call(parameters=["cactus_fastaGenerator",
                            "--flowerName", str(flowerName),
                            "--outputFile", outputFile,
                            "--logLevel", logLevel,
                            "--cactusDisk", cactusDiskDatabaseString,
                            "--referenceEventString", referenceEventString])

def runCactusAnalyseAssembly(sequenceFile):
    return cactus_call(check_output=True,
                parameters=["cactus_analyseAssembly",
                            sequenceFile])[:-1]

def runToilStats(toil, outputFile):
    system("toil stats %s --outputFile %s" % (toil, outputFile))
    logger.info("Ran the job-tree stats command apparently okay")

def runLastz(seq1, seq2, alignmentsFile, lastzArguments, work_dir=None, gpuLastz=False):
    if work_dir is None:
        assert os.path.dirname(seq1) == os.path.dirname(seq2)
        work_dir = os.path.dirname(seq1)
    if gpuLastz == True:
        lastzCommand = "run_segalign"
    else:
        lastzCommand = "cPecanLastz"
        seq1 += "[multiple][nameparse=darkspace]"
        seq2 += "[nameparse=darkspace]"
    cactus_call(work_dir=work_dir, outfile=alignmentsFile,
                parameters=[lastzCommand, seq1, seq2, "--format=cigar", "--notrivial"] + lastzArguments.split())

def runSelfLastz(seq, alignmentsFile, lastzArguments, work_dir=None, gpuLastz=False):
    return runLastz(seq, seq, alignmentsFile, lastzArguments, work_dir, gpuLastz)

def runCactusRealign(seq1, seq2, inputAlignmentsFile, outputAlignmentsFile, realignArguments, work_dir=None):
    cactus_call(infile=inputAlignmentsFile, outfile=outputAlignmentsFile, work_dir=work_dir,
                parameters=["cPecanRealign"] + realignArguments.split() + [seq1, seq2])

def runCactusSelfRealign(seq, inputAlignmentsFile, outputAlignmentsFile, realignArguments, work_dir=None):
    cactus_call(infile=inputAlignmentsFile, outfile=outputAlignmentsFile, work_dir=work_dir,
                parameters=["cPecanRealign"] + realignArguments.split() + [seq])

def runCactusCoverage(sequenceFile, alignmentsFile, work_dir=None):
    return cactus_call(check_output=True, work_dir=work_dir,
                parameters=["cactus_coverage", sequenceFile, alignmentsFile])

def runGetChunks(sequenceFiles, chunksDir, chunkSize, overlapSize, work_dir=None):
    chunks = cactus_call(work_dir=work_dir,
                         check_output=True,
                         parameters=["cactus_blast_chunkSequences",
                                     getLogLevelString(),
                                     str(chunkSize),
                                     str(overlapSize),
                                     chunksDir] + sequenceFiles)
    return [chunk for chunk in chunks.split("\n") if chunk != ""]

def pullCactusImage():
    """Ensure that the cactus Docker image is pulled."""
    if os.environ.get('CACTUS_DOCKER_MODE') == "0":
        return
    if os.environ.get('CACTUS_USE_LOCAL_IMAGE', 0) == "1":
        return
    image = getDockerImage()
    call = ["docker", "pull", image]
    process = subprocess.Popen(call, stdout=subprocess.PIPE,
                                 stderr=sys.stderr, bufsize=-1)
    output, _ = process.communicate()
    if process.returncode != 0:
        raise RuntimeError("Command %s failed with output: %s" % (call, output))

def getDockerOrg():
    """Get where we should find the cactus containers."""
    if "CACTUS_DOCKER_ORG" in os.environ:
        return os.environ["CACTUS_DOCKER_ORG"]
    else:
        return "quay.io/comparative-genomics-toolkit"

def getDockerTag():
    """Get what docker tag we should use for the cactus image
    (either forced to be latest or the current cactus commit)."""
    if 'CACTUS_USE_LATEST' in os.environ:
        return "latest"
    else:
        return cactus_commit

def getDockerImage():
    """Get fully specified Docker image name."""
    return "%s/cactus:%s" % (getDockerOrg(), getDockerTag())

def getDockerRelease(gpu=False):
    """Get the most recent docker release."""
    r = "quay.io/comparative-genomics-toolkit/cactus:v1.2.3"
    if gpu:
        r += "-gpu"
    return r

def maxMemUsageOfContainer(containerInfo):
    """Return the max RSS usage (in bytes) of a container, or None if something failed."""
    if containerInfo['id'] is None:
        # Try to get the internal container ID from the docker name
        try:
            id = popenCatch("docker inspect -f '{{.Id}}' %s" % containerInfo['name']).strip()
            containerInfo['id'] = id
        except:
            # Not yet running
            return None
    # Try to check for the maximum memory usage ever used by that
    # container, in a few different possible locations depending on
    # the distribution
    possibleLocations = ["/sys/fs/cgroup/memory/docker/%s/memory.max_usage_in_bytes",
                         "/sys/fs/cgroup/memory/system.slice.docker-%s.scope/memory.max_usage_in_bytes"]
    possibleLocations = [s % containerInfo['id'] for s in possibleLocations]
    for location in possibleLocations:
        try:
            with open(location) as f:
                return int(f.read())
        except IOError:
            # Not at this location, or sysfs isn't mounted
            continue
    return None

# send a time/date stamped message to the realtime logger, truncating it
# if it's too long (so it's less likely to be dropped)
def cactus_realtime_log(msg, max_len = 1500, log_debug=False):
    if len(msg) > max_len:
        msg = msg[:max_len-207] + " <...> " + msg[-200:]
    if not log_debug:
        RealtimeLogger.info("{}: {}".format(datetime.now(), msg))
    else:
        RealtimeLogger.debug("{}: {}".format(datetime.now(), msg))
        
def setupBinaries(options):
    """Ensure that Cactus's C/C++ components are ready to run, and set up the environment."""
    if options.latest:
        os.environ["CACTUS_USE_LATEST"] = "1"
    if options.binariesMode is not None:
        # Mode is specified on command line
        mode = options.binariesMode
    else:
        # Might be specified through the environment, or not, in which
        mode = os.environ.get("CACTUS_BINARIES_MODE")

    def verify_docker():
        # If running without Docker, verify that we can find the Cactus executables
        from distutils.spawn import find_executable
        if find_executable('docker') is None:
            raise RuntimeError("The `docker` executable wasn't found on the "
                               "system. Please install Docker if possible, or "
                               "use --binariesMode local and add cactus's bin "
                               "directory to your PATH.")
    def verify_local():
        from distutils.spawn import find_executable
        if find_executable('cactus_caf') is None:
            raise RuntimeError("Cactus isn't using Docker, but it can't find "
                               "the Cactus binaries. Please add Cactus's bin "
                               "directory to your PATH (and run `make` in the "
                               "Cactus directory if you haven't already).")
        if find_executable('ktserver') is None:
            raise RuntimeError("Cactus isn't using Docker, but it can't find "
                               "`ktserver`, the KyotoTycoon database server. "
                               "Please install KyotoTycoon "
                               "(https://github.com/alticelabs/kyoto) "
                               "and add the binary to your PATH, or use the "
                               "Docker mode.")

    if mode is None:
        # there is no mode set, we use local if it's available, otherwise default to docker
        try:
            verify_local()
            mode = "local"
        except:
            verify_docker()
            mode = "docker"
    elif mode == "docker":
        verify_docker()
    elif mode == "local":
        verify_local()
    else:
        assert mode == "singularity"
        jobStoreType, locator = Toil.parseLocator(options.jobStore)
        if jobStoreType == "file":
            # if not using a local jobStore, then don't set the `SINGULARITY_CACHEDIR`
            # in this case, the image will be downloaded on each call
            if options.containerImage:
                imgPath = os.path.abspath(options.containerImage)
                os.environ["CACTUS_USE_LOCAL_SINGULARITY_IMG"] = "1"
            else:
                # When SINGULARITY_CACHEDIR is set, singularity will refuse to store images in the current directory
                if 'SINGULARITY_CACHEDIR' in os.environ:
                    imgPath = os.path.join(os.environ['SINGULARITY_CACHEDIR'], "cactus.img")
                else:
                    imgPath = os.path.join(os.path.abspath(locator), "cactus.img")
            os.environ["CACTUS_SINGULARITY_IMG"] = imgPath

    os.environ["CACTUS_BINARIES_MODE"] = mode

def importSingularityImage(options):
    """Import the Singularity image from Docker if using Singularity."""
    mode = os.environ.get("CACTUS_BINARIES_MODE", "docker")
    localImage = os.environ.get("CACTUS_USE_LOCAL_SINGULARITY_IMG", "0")
    if mode == "singularity" and Toil.parseLocator(options.jobStore)[0] == "file":
        imgPath = os.environ["CACTUS_SINGULARITY_IMG"]
        # If not using local image, pull the docker image
        if localImage == "0":
            # Singularity will complain if the image file already exists. Remove it.
            try:
                os.remove(imgPath)
            except OSError:
                # File doesn't exist
                pass
            # Singularity 2.4 broke the functionality that let --name
            # point to a path instead of a name in the CWD. So we change
            # to the proper directory manually, then change back after the
            # image is pulled.
            # NOTE: singularity writes images in the current directory only
            #       when SINGULARITY_CACHEDIR is not set
            oldCWD = os.getcwd()
            os.chdir(os.path.dirname(imgPath))
            # --size is deprecated starting in 2.4, but is needed for 2.3 support. Keeping it in for now.
            try:
                subprocess.check_call(["singularity", "pull", "--size", "2000", "--name", os.path.basename(imgPath),
                                       "docker://" + getDockerImage()])
            except subprocess.CalledProcessError:
                # Call failed, try without --size, required for singularity 3+
                subprocess.check_call(["singularity", "pull", "--name", os.path.basename(imgPath),
                                       "docker://" + getDockerImage()])
            os.chdir(oldCWD)
        else:
            logger.info("Using pre-built singularity image: '{}'".format(imgPath))

def singularityCommand(tool=None,
                       work_dir=None,
                       parameters=None,
                       port=None,
                       file_store=None):
    if "CACTUS_SINGULARITY_IMG" in os.environ:
        # old logic: just run a local image
        # (this was toggled by only setting CACTUS_SINGULARITY_IMG when using a local jobstore in cactus_progressive.py)
        base_singularity_call = ["singularity", "--silent", "run", os.environ["CACTUS_SINGULARITY_IMG"]]
        base_singularity_call.extend(parameters)
        return base_singularity_call
    else:
        # workaround for kubernetes toil: explicitly make a local image
        # (see https://github.com/vgteam/toil-vg/blob/master/src/toil_vg/singularity.py)

        if parameters is None:
            parameters = []
        if work_dir is None:
            work_dir = os.getcwd()

        baseSingularityCall = ['singularity', '-q', 'exec']

        # Mount workdir as /mnt and work in there.
        # Hope the image actually has a /mnt available.
        # Otherwise this silently doesn't mount.
        # But with -u (user namespaces) we have no luck pointing in-container
        # home at anything other than our real home (like something under /var
        # where Toil puts things).
        # Note that we target Singularity 3+.
        baseSingularityCall += ['-u', '-B', '{}:{}'.format(os.path.abspath(work_dir), '/mnt'), '--pwd', '/mnt']

        # Problem: Multiple Singularity downloads sharing the same cache directory will
        # not work correctly. See https://github.com/sylabs/singularity/issues/3634
        # and https://github.com/sylabs/singularity/issues/4555.

        # As a workaround, we have out own cache which we manage ourselves.
        home_dir = str(pathlib.Path.home())
        default_singularity_dir = os.path.join(home_dir, '.singularity')
        cache_dir = os.path.join(os.environ.get('SINGULARITY_CACHEDIR',  default_singularity_dir), 'toil')
        os.makedirs(cache_dir, exist_ok=True)

        # hack to transform back to docker image
        if tool == 'cactus':
            tool = getDockerImage()
        # not a url or local file? try it as a Docker specifier
        if not tool.startswith('/') and '://' not in tool:
            tool = 'docker://' + tool

        # What name in the cache dir do we want?
        # We cache everything as sandbox directories and not .sif files because, as
        # laid out in https://github.com/sylabs/singularity/issues/4617, there
        # isn't a way to run from a .sif file and have write permissions on system
        # directories in the container, because the .sif build process makes
        # everything owned by root inside the image. Since some toil-vg containers
        # (like the R one) want to touch system files (to install R packages at
        # runtime), we do it this way to act more like Docker.
        #
        # Also, only sandbox directories work with user namespaces, and only user
        # namespaces work inside unprivileged Docker containers like the Toil
        # appliance.
        sandbox_dirname = os.path.join(cache_dir, '{}.sandbox'.format(hashlib.sha256(tool.encode('utf-8')).hexdigest()))

        if not os.path.exists(sandbox_dirname):
            # We atomically drop the sandbox at that name when we get it

            # Make a temp directory to be the sandbox
            temp_sandbox_dirname = tempfile.mkdtemp(dir=cache_dir)

            # Download with a fresh cache to a sandbox
            download_env = os.environ.copy()
            download_env['SINGULARITY_CACHEDIR'] = file_store.getLocalTempDir() if file_store else tempfile.mkdtemp(dir=work_dir)
            build_cmd = ['singularity', 'build', '-s', '-F', temp_sandbox_dirname, tool]

            cactus_realtime_log("Running the command: \"{}\"".format(' '.join(build_cmd)))
            start_time = time.time()
            subprocess.check_call(build_cmd, env=download_env)
            run_time = time.time() - start_time
            cactus_realtime_log("Successfully ran the command: \"{}\" in {} seconds".format(' '.join(build_cmd), run_time))

            # Clean up the Singularity cache since it is single use
            shutil.rmtree(download_env['SINGULARITY_CACHEDIR'])

            try:
                # This may happen repeatedly but it is atomic
                os.rename(temp_sandbox_dirname, sandbox_dirname)
            except OSError as e:
                if e.errno == errno.EEXIST:
                    # Can't rename a directory over another
                    # Make sure someone else has made the directory
                    assert os.path.exists(sandbox_dirname)
                    # Remove our redundant copy
                    shutil.rmtree(temp_sandbox_dirname)
                else:
                    raise

            # TODO: we could save some downloading by having one process download
            # and the others wait, but then we would need a real fnctl locking
            # system here.
        return baseSingularityCall + [sandbox_dirname] + parameters


def dockerCommand(tool=None,
                  work_dir=None,
                  parameters=None,
                  rm=True,
                  port=None,
                  dockstore=None,
                  entrypoint=None):
    # This is really dumb, but we have to work around an intersection
    # between two bugs: one in CoreOS where /etc/resolv.conf is
    # sometimes missing temporarily, and one in Docker where it
    # refuses to start without /etc/resolv.conf.
    while not os.path.exists('/etc/resolv.conf'):
        pass

    base_docker_call = ['docker', 'run',
                        '--interactive',
                        '--net=host',
                        '--log-driver=none',
                        '-u', '%s:%s' % (os.getuid(), os.getgid()),
                        '-v', '{}:/data'.format(os.path.abspath(work_dir))]

    if entrypoint is not None:
        base_docker_call += ['--entrypoint', entrypoint]
    else:
        base_docker_call += ['--entrypoint', '/opt/cactus/wrapper.sh']

    if port is not None:
        base_docker_call += ["-p", "%d:%d" % (port, port)]

    containerInfo = { 'name': str(uuid.uuid4()), 'id': None }
    base_docker_call.extend(['--name', containerInfo['name']])
    if rm:
        base_docker_call.append('--rm')

    docker_tag = getDockerTag()
    tool = "%s/%s:%s" % (dockstore, tool, docker_tag)
    call = base_docker_call + [tool] + parameters
    return call, containerInfo

def prepareWorkDir(work_dir, parameters):
    if not work_dir:
        # Make sure all the paths we're accessing are in the same directory
        files = [par for par in parameters if os.path.isfile(par)]
        folders = [par for par in parameters if os.path.isdir(par)]
        work_dirs = set([os.path.dirname(fileName) for fileName in files] + [os.path.dirname(folder) for folder in folders])
        _log.info("Work dirs: %s" % work_dirs)
        if len(work_dirs) > 1:
            work_dir = os.path.commonprefix(list(work_dirs))
        elif len(work_dirs) == 1:
            work_dir = work_dirs.pop()

    #If there are no input files, or if their MRCA is '' (when working
    #with relative paths), just set the current directory as the work
    #dir
    if work_dir is None or work_dir == '':
        work_dir = "."
    _log.info("Docker work dir: %s" % work_dir)

    #We'll mount the work_dir containing the paths as /data in the container,
    #so set all the paths to their basenames. The container will access them at
    #/data/<path>
    def adjustPath(path, wd):
        # Hack to relativize paths that are not provided as a
        # single argument (i.e. multiple paths that are
        # space-separated and quoted)
        if wd != '.':
            if not wd.endswith('/'):
                wd = wd + '/'
            return path.replace(wd, '')
        else:
            return path

    if work_dir and os.environ.get('CACTUS_DOCKER_MODE', 1) != "0":
        parameters = [adjustPath(par, work_dir) for par in parameters]
    return work_dir, parameters

def cactus_call(tool=None,
                work_dir=None,
                parameters=None,
                rm=True,
                check_output=False,
                infile=None,
                outfile=None,
                outappend=False,
                stdin_string=None,
                server=False,
                shell=False,
                port=None,
                check_result=False,
                dockstore=None,
                soft_timeout=None,
                job_name=None,
                features=None,
                fileStore=None,
                swallowStdErr=False):
    mode = os.environ.get("CACTUS_BINARIES_MODE", "docker")
    if dockstore is None:
        dockstore = getDockerOrg()
    if parameters is None:
        parameters = []
    if tool is None:
        tool = "cactus"
    
    entrypoint = None
    if (len(parameters) > 0) and isinstance(parameters[0], list):
        # We have a list of lists, which is the convention for commands piped into one another.
        flattened = [i for sublist in parameters for i in sublist]
        chain_params = [' '.join(p) for p in [list(map(pipes.quote, q)) for q in parameters]]
        parameters = ['bash', '-c', 'set -eo pipefail && ' + ' | '.join(chain_params)]
        if mode == "docker":
            # We want to shell into bash directly rather than going
            # through the default cactus entrypoint.
            entrypoint = '/bin/bash'
            parameters = parameters[1:]
            work_dir, _ = prepareWorkDir(work_dir, flattened)

    if mode in ("docker", "singularity"):
        work_dir, parameters = prepareWorkDir(work_dir, parameters)

    if mode == "docker":
        call, containerInfo = dockerCommand(tool=tool,
                                            work_dir=work_dir,
                                            parameters=parameters,
                                            rm=rm,
                                            port=port,
                                            dockstore=dockstore,
                                            entrypoint=entrypoint)
    elif mode == "singularity":
        call = singularityCommand(tool=tool, work_dir=work_dir,
                                  parameters=parameters, port=port, file_store=fileStore)
    else:
        assert mode == "local"
        call = parameters

    if stdin_string:
        stdinFileHandle = subprocess.PIPE
    elif infile:
        stdinFileHandle = open(infile, 'r')
    else:
        stdinFileHandle = subprocess.DEVNULL
    stdoutFileHandle = None
    if outfile:
        stdoutFileHandle = open(outfile, 'a' if outappend else 'w')
    if check_output:
        stdoutFileHandle = subprocess.PIPE

    _log.info("Running the command %s" % call)
    rt_message = 'Running the command: \"{}\"'.format(' '.join(call))
    if features:
        rt_message += ' (features={})'.format(features)
    cactus_realtime_log(rt_message, log_debug = 'ktremotemgr' in call)

    # hack to keep track of memory usage for single machine
    time_v = os.environ.get("CACTUS_LOG_MEMORY") is not None and 'ktserver' not in call and 'redis-server' not in call

    # use /usr/bin/time -v to get peak memory usage
    if time_v:
        if not shell:
            shell = True
            call = ' '.join(shlex.quote(t) for t in call)
        swallowStdErr = True
        call = '/usr/bin/time -v {}'.format(call)
        
    process = subprocess.Popen(call, shell=shell, encoding="ascii",
                               stdin=stdinFileHandle, stdout=stdoutFileHandle,
                               stderr=subprocess.PIPE if swallowStdErr else sys.stderr,
                               bufsize=-1, cwd=work_dir)

    if server:
        return process

    memUsage = 0
    first_run = True
    start_time = time.time()
    output = stderr = None  # used later to report errors
    while True:
        try:
            # Wait a bit to see if the process is done
            output, stderr = process.communicate(stdin_string if first_run else None, timeout=10)
        except subprocess.TimeoutExpired:
            if mode == "docker":
                # Every so often, check the memory usage of the container
                updatedMemUsage = maxMemUsageOfContainer(containerInfo)
                if updatedMemUsage is not None:
                    assert memUsage <= updatedMemUsage, "memory.max_usage_in_bytes should never decrease"
                    memUsage = updatedMemUsage
            first_run = False
            if soft_timeout is not None and time.time() - start_time > soft_timeout:
                # Soft timeout has been triggered. Just return early.
                process.send_signal(signal.SIGINT)
                return None
        else:
            break
    if mode == "docker" and job_name is not None and features is not None and fileStore is not None:
        # Log a datapoint for the memory usage for these features.
        fileStore.logToMaster("Max memory used for job %s (tool %s) "
                              "on JSON features %s: %s" % (job_name, parameters[0],
                                                           json.dumps(features), memUsage))

    if process.returncode == 0:
        run_time = time.time() - start_time
        if time_v:
            call = call[len("/usr/bin/time -v "):]
        rt_message = "Successfully ran: \"{}\"".format(' '.join(call) if not shell else call)
        if features:
            rt_message += ' (features={})'.format(features)
        rt_message += " in {} seconds".format(round(run_time, 4))
        if time_v:            
            for line in stderr.split('\n'):
                if 'Maximum resident set size (kbytes):' in line:
                    rt_message += ' and {} memory'.format(bytes2human(int(line.split()[-1]) * 1024))
                    break
        cactus_realtime_log(rt_message, log_debug = 'ktremotemgr' in call)

    if check_result:
        return process.returncode

    if process.returncode != 0:
        out = "stdout={}".format(output)
        if swallowStdErr:
            out += ", stderr={}".format(stderr)
        if process.returncode > 0:
            raise RuntimeError("Command {} exited {}: {}".format(call, process.returncode, out))
        else:
            raise RuntimeError("Command {} signaled {}: {}".format(call, signal.Signals(-process.returncode).name, out))

    if check_output:
        return output

class RunAsFollowOn(Job):
    def __init__(self, job, *args, **kwargs):
        Job.__init__(self, cores=0.1, memory=100000000, preemptable=True)
        self._args = args
        self._kwargs = kwargs
        self.job = job

    def run(self, fileStore):
        return self.addFollowOn(self.job(*self._args, **self._kwargs)).rv()

class RoundedJob(Job):
    """Thin wrapper around Toil.Job to round up resource requirements.

    Rounding is useful to make Toil's Mesos scheduler more
    efficient--it runs a process that is O(n log n) in the number of
    different resource requirements for every offer received, so
    thousands of slightly different requirements will slow down the
    leader and the workflow.
    """
    # Default rounding amount: 100 MiB
    roundingAmount = 100*1024*1024
    def __init__(self, memory=None, cores=None, disk=None, preemptable=None,
                 unitName=None, checkpoint=False):
        if memory is not None:
            memory = self.roundUp(memory)
        if disk is not None:
            # hack: we may need extra space to cook up a singularity image on the fly
            #       so we add it (1.5G) here.
            # todo: only do this when needed
            disk = 1500*1024*1024 + self.roundUp(disk)
        super(RoundedJob, self).__init__(memory=memory, cores=cores, disk=disk,
                                         preemptable=preemptable, unitName=unitName,
                                         checkpoint=checkpoint)

    def roundUp(self, bytesRequirement):
        """
        Round the amount up to the next self.roundingAmount.

        >>> j = RoundedJob()
        >>> j.roundingAmount = 100000000
        >>> j.roundUp(1000)
        10000000
        >>> j.roundUp(200000000)
        200000000
        >>> j.roundUp(200000001)
        300000000
        """
        if bytesRequirement % self.roundingAmount == 0:
            return bytesRequirement
        return (bytesRequirement // self.roundingAmount + 1) * self.roundingAmount

    def _runner(self, jobGraph, jobStore, fileStore, defer=None):
        if jobStore.config.workDir is not None:
            os.environ['TMPDIR'] = fileStore.getLocalTempDir()
        if defer:
            # Toil v 3.21 or later
            super(RoundedJob, self)._runner(jobGraph=jobGraph, jobStore=jobStore, fileStore=fileStore, defer=defer)
        else:
            # Older versions of toil
            super(RoundedJob, self)._runner(jobGraph=jobGraph, jobStore=jobStore, fileStore=fileStore)

def readGlobalFileWithoutCache(fileStore, jobStoreID):
    """Reads a jobStoreID into a file and returns it, without touching
    the cache.

    Works around toil issue #1532.
    """
    f = fileStore.getLocalTempFile()
    fileStore.jobStore.readFile(jobStoreID, f)
    return f

class ChildTreeJob(RoundedJob):
    """Spreads the child-job initialization work among multiple jobs.

    Jobs with many children can often be a bottleneck (because they
    are written serially into the jobStore in a consistent-write
    fashion). Subclasses of this job will automatically spread out
    that work amongst a tree of jobs, increasing the total work done
    slightly, but reducing the wall-clock time taken dramatically.
    """
    def __init__(self, memory=None, cores=None, disk=None, preemptable=None,
                 unitName=None, checkpoint=False, maxChildrenPerJob=20):
        self.queuedChildJobs = []
        self.maxChildrenPerJob = maxChildrenPerJob
        super(ChildTreeJob, self).__init__(memory=memory, cores=cores, disk=disk,
                                           preemptable=preemptable, unitName=unitName,
                                           checkpoint=checkpoint)

    def addChild(self, job):
        self.queuedChildJobs.append(job)
        return job

    def _run(self, jobGraph, fileStore):
        ret = super(ChildTreeJob, self)._run(jobGraph, fileStore)
        if len(self.queuedChildJobs) <= self.maxChildrenPerJob:
            # The number of children is small enough that we can just
            # add them directly.
            for childJob in self.queuedChildJobs:
                super(ChildTreeJob, self).addChild(childJob)
        else:
            # Too many children, so we have to build a tree to avoid
            # bottlenecking on consistently serializing all the jobs.

            # compute the number of levels (after root) of our job tree
            num_levels = math.floor(math.log(len(self.queuedChildJobs), self.maxChildrenPerJob))

            # fill out all the internal nodes of the tree, where the root is self
            # they will be empty RoundedJobs
            prev_level = [self]
            level = []
            for i in range(num_levels):
                for parent_job in prev_level:
                    # with this check, we allow a partial split of the last level
                    # to account for rounding
                    if len(level) * self.maxChildrenPerJob < len(self.queuedChildJobs):
                        for j in range(self.maxChildrenPerJob):
                            child_job = RoundedJob()
                            if parent_job is self:
                                super(ChildTreeJob, self).addChild(child_job)
                            else:
                                parent_job.addChild(child_job)
                            level.append(child_job)
                    else:
                        level.append(parent_job)

                prev_level = level
                level = []

            # add the leaves.  these will be the jobs in self.queuedChildJobs
            leaves_added = 0
            for parent_job in prev_level:
                num_children = min(len(self.queuedChildJobs) - leaves_added, self.maxChildrenPerJob)
                for j in range(num_children):
                    if parent_job is self:
                        super(ChildTreeJob, self).addChild(self.queuedChildJobs[leaves_added])
                    else:
                        parent_job.addChild(self.queuedChildJobs[leaves_added])
                    leaves_added += 1
            assert leaves_added == len(self.queuedChildJobs)

        return ret

def dumpStacksHandler(signal, frame):
    """Signal handler to print the stacks of all threads to stderr"""
    fh = sys.stderr
    print("###### stack traces {} ######".format(datetime.now().isoformat()), file=fh)
    id2name = dict([(th.ident, th.name) for th in threading.enumerate()])
    for threadId, stack in sys._current_frames().items():
        print("# Thread: {}({})".format(id2name.get(threadId,""), threadId), file=fh)
        traceback.print_stack(f=stack, file=fh)
    print("\n", file=fh)
    fh.flush()

def enableDumpStack(sig=signal.SIGUSR1):
    """enable dumping stacks when the specified signal is received"""
    signal.signal(sig, dumpStacksHandler)

def unzip_gzs(job, input_paths, input_ids):
    """ go through a list of files and unzip any that end with .gz and return a list 
    of updated ids.  files that don't end in .gz are just passed through.  relying on the extension
    is pretty fragile but better than nothing """
    unzipped_ids = []
    for input_path, input_id in zip(input_paths, input_ids):
        if input_path.endswith('.gz'):
            unzip_job = job.addChildJobFn(unzip_gz, input_path, input_id, disk=10*input_id.size)
            unzipped_ids.append(unzip_job.rv())
        else:
            unzipped_ids.append(input_id)
    return unzipped_ids

def unzip_gz(job, input_path, input_id):
    """ unzip a single file """
    work_dir = job.fileStore.getLocalTempDir()
    assert input_path.endswith('.gz')
    fa_path = os.path.join(work_dir, os.path.basename(input_path))
    job.fileStore.readGlobalFile(input_id, fa_path, mutable=True)
    cactus_call(parameters=['gzip', '-d', os.path.basename(fa_path)], work_dir=work_dir)
    return job.fileStore.writeGlobalFile(fa_path[:-3])

def zip_gzs(job, input_paths, input_ids, list_elems = None):
    """ zip up some files.  the input_ids can be a list of lists.  if it is, then list_elems
    can be used to only zip a subset (leaving everything else) on each list."""
    zipped_ids = []
    for input_path, input_list in zip(input_paths, input_ids):
        if input_path.endswith('.gz'):
            try:
                iter(input_list)
                is_list = True
            except:
                is_list = False
            if is_list:
                output_list = []
                for i, elem in enumerate(input_list):
                    if not list_elems or i in list_elems:
                        output_list.append(job.addChildJobFn(zip_gz, input_path, elem, disk=2*elem.size).rv())
                    else:
                        output_list.append(elem)
                zipped_ids.append(output_list)
            else:
                zipped_ids.append(job.addChildJobFn(zip_gz, input_path, input_id, disk=2*input_id.size).rv())
        else:
            zipped_ids.append(input_list)
    return zipped_ids
    
def zip_gz(job, input_path, input_id):
    """ zip a single file """
    work_dir = job.fileStore.getLocalTempDir()
    fa_path = os.path.join(work_dir, os.path.basename(input_path))
    if fa_path.endswith('.gz'):
        fa_path = fa_path[:-3]
    job.fileStore.readGlobalFile(input_id, fa_path, mutable=True)
    cactus_call(parameters=['gzip', os.path.basename(fa_path)], work_dir=work_dir)
    return job.fileStore.writeGlobalFile(fa_path + '.gz')

def get_aws_region(full_path):
    """ parse aws:region:url  to just get region (toil surely has better way to do this but in rush)"""
    if full_path.startswith('aws:'):
        return full_path.split(':')[1]
    else:
        return None

def write_s3(local_path, s3_path, region=None):
    """ cribbed from toil-vg.  more convenient just to throw hal output on s3
    than pass it as a promise all the way back to the start job to export it locally """
    assert s3_path.startswith('s3://')
    bucket_name, name_prefix = s3_path[5:].split("/", 1)
    botocore_session = botocore.session.get_session()
    botocore_session.get_component('credential_provider').get_provider('assume-role').cache = botocore.credentials.JSONFileCache()
    boto3_session = boto3.Session(botocore_session=botocore_session)

    # Connect to the s3 bucket service where we keep everything
    s3 = boto3_session.client('s3')
    try:
        s3.head_bucket(Bucket=bucket_name)
    except:
        if region:
            s3.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={'LocationConstraint':region})
        else:
            s3.create_bucket(Bucket=bucket_name)

    s3.upload_file(local_path, bucket_name, name_prefix)
