#!/usr/bin/env python

#from pyadh import *
#from proteus.iproteus import *
from ctypes import *
import numpy
from proteus import MeshTools
from proteus import cmeshTools
from proteus.MeshAdaptPUMI import MeshAdaptPUMI
from proteus import Archiver
from proteus import Domain
from tables import *

from petsc4py import PETSc

import os
print os.getcwd()

testDir='./proteus/MeshAdaptPUMI/test/errorCheck/'
Model=testDir + 'Couette.smd'
Mesh=testDir + 'Couette.smb'

domain = Domain.PUMIDomain() #initialize the domain
domain.PUMIMesh=MeshAdaptPUMI.MeshAdaptPUMI(hmax=0.01, hmin=0.008, numIter=1,sfConfig='alvin',maType='isotropic',logType="on")
domain.PUMIMesh.loadModelAndMesh(Model, Mesh)
domain.PUMIMesh.simmetrixBCreloaded(Model)
domain.faceList=[[80],[76],[42],[24],[82],[78]]

mesh = MeshTools.TetrahedralMesh()
mesh.cmesh = cmeshTools.CMesh()
comm = Comm.init()

mesh.convertFromPUMI(domain.PUMIMesh, domain.faceList, parallel = comm.size() > 1, dim = domain.nd)

print "Done reading in mesh"
print "meshInfo says : \n", mesh.meshInfo()

domain.PUMIMesh.transferFieldToPUMI("coordinates",mesh.nodeArray)

rho = numpy.array([998.2,998.2])
nu = numpy.array([1.004e-6, 1.004e-6])
g = numpy.asarray([0.0,0.0,0.0])
domain.PUMIMesh.transferPropertiesToPUMI(rho,nu,g)

#Couette Flow

print "Couette Flow"
Lz = 0.05
Uinf = 2e-3

vector=numpy.zeros((mesh.nNodes_global,3),'d')
dummy = numpy.zeros(mesh.nNodes_global); 
vector[:,0] = dummy
vector[:,1] = Uinf*mesh.nodeArray[:,2]/Lz #v-velocity
vector[:,2] = dummy
domain.PUMIMesh.transferFieldToPUMI("velocity", vector)
del vector
del dummy

scalar=numpy.zeros((mesh.nNodes_global,1),'d')
domain.PUMIMesh.transferFieldToPUMI("p", scalar)

scalar[:,0] = mesh.nodeArray[:,2]
domain.PUMIMesh.transferFieldToPUMI("phi", scalar)
del scalar

scalar = numpy.zeros((mesh.nNodes_global,1),'d')+1.0
domain.PUMIMesh.transferFieldToPUMI("vof", scalar)

domain.PUMIMesh.get_local_error()
if(domain.PUMIMesh.willAdapt()):
  print "Success!"

domain.PUMIMesh.adaptPUMIMesh()



#mesh = MeshTools.TetrahedralMesh()
#mesh.convertFromPUMI(domain.PUMIMesh,
#                 domain.faceList,
#                 parallel = comm.size() > 1,
#                 dim = domain.nd)
#mlMesh = MeshTools.MultilevelTetrahedralMesh(
#  0,0,0,skipInit=True,
#  nLayersOfOverlap=0,
#  parallelPartitioningType=MeshTools.MeshParallelPartitioningTypes.element)

#mlMesh.generateFromExistingCoarseMesh(
#    mesh,1,
#    nLayersOfOverlap=0,
#    parallelPartitioningType=MeshTools.MeshParallelPartitioningTypes.element)


'''
#Poiseuille Flow
print "Poiseuille Flow"
Lz = 0.05;
Ly = 0.2;
Uinf =  0.5/0.0010021928*(1/Ly)

vector=numpy.zeros((mesh.nNodes_global,3),'d')
dummy = numpy.zeros(mesh.nNodes_global); 
vector[:,0] = dummy
vector[:,1] = -Uinf*(mesh.nodeArray[:,2]*mesh.nodeArray[:,2]-Lz*mesh.nodeArray[:,2]) #v-velocity
vector[:,2] = dummy
domain.PUMIMesh.transferFieldToPUMI("velocity", vector)
del vector
del dummy

scalar=numpy.zeros((mesh.nNodes_global,1),'d')
scalar[:,0] = 1-mesh.nodeArray[:,1]/Ly
domain.PUMIMesh.transferFieldToPUMI("p", scalar)
del scalar

scalar=numpy.zeros((mesh.nNodes_global,1),'d')
scalar[:,0] = mesh.nodeArray[:,2]
domain.PUMIMesh.transferFieldToPUMI("phi", scalar)
del scalar

scalar = numpy.zeros((mesh.nNodes_global,1),'d')+1.0
domain.PUMIMesh.transferFieldToPUMI("vof", scalar)

domain.PUMIMesh.adaptPUMIMesh()
'''