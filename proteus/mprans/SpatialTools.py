"""
This module adds functionality to proteus.SpatialTools module by enabling
two-phase flow functionality such as converting shapes to moving rigid bodies,
or adding wave absorption and generation zones.


Example
-------
from proteus import Domain
from proteus.mprans import SpatialTools as st
import numpy as np

domain = Domain.PlanarStraightLineGraphDomain()
tank = st.Tank2D(domain. dim=[4., 4.])
tank.setSponge(left=0.4)
tank.setAbsorptionZones(left=true)
shape = st.Rectangle(domain, dim=[0.5, 0.5], coords=[1., 1.])
shape.setRigidBody()
shape2.rotate(np.pi/3.)
shape2.BC.left.setNoSlip()

st.assembleDomain(domain)
"""

from math import cos, sin, sqrt, atan2, acos, asin
from itertools import compress
import csv
import os
import numpy as np
from proteus import AuxiliaryVariables, Archiver, Comm, Profiling, Gauges
from proteus.Profiling import logEvent
from proteus.mprans import BoundaryConditions as bc
from proteus.SpatialTools import (Shape,
                                  Cuboid,
                                  Sphere,
                                  Rectangle,
                                  CustomShape,
                                  ShapeSTL,
                                  BCContainer,
                                  _assembleGeometry,
                                  _generateMesh)


class ShapeRANS(Shape):
    """
    Base/super class of all shapes. Sets the boundary condition class to
    proteus.mprans.BoundaryConditions.BC_RANS.

    Parameters
    ----------
    domain: proteus.Domain.D_base
        Domain class instance that hold all the geometrical informations and
        boundary conditions of the shape.
    nd: Optional[int]
        Number of dimensions of the shape. If not set, will take the number of
        dimensions of the domain.
    """

    def __init__(self, domain, nd):
        super(ShapeRANS, self).__init__(domain, nd, BC_class=bc.BC_RANS)
        self.mass = None
        self.density = None
        self.free_x = (1, 1, 1)
        self.free_r = (1, 1, 1)
        self.record_values = False
        self.zones = {}  # for absorption/generation zones
        self.auxiliaryVariables = {}  # list of auxvar attached to shape
        self.It = None  # inertia tensor

    def _attachAuxiliaryVariable(self, key, gauge=None):
        """
        Attaches an auxiliary variable to the auxiliaryVariables dictionary of
        the shape (used in buildDomain function)

        Parameters
        ----------
        key: string
            Dictionary key defining the auxiliaryVariable to attach

        gauge: Gauges

        Notes
        -----
        This function is called automatically when using other functions to set
        auxiliaryVariables and should not be used manually.
        """
        if key not in self.auxiliaryVariables:
            if key == 'RigidBody':
                self.auxiliaryVariables[key] = True
            elif key == 'RelaxZones':
                self.auxiliaryVariables[key] = self.zones
            elif str(key).startswith('Gauge_'):
                self.auxiliaryVariables[key] = [gauge]
            else:
                logEvent("auxiliaryVariable key: "
                         "{key} not recognized.".format(key=str(key)), level=1)
        elif str(key).startswith('Gauge_'):
            if gauge not in self.auxiliaryVariables[key]:
                self.auxiliaryVariables[key] += [gauge]
            else:
                logEvent(
                    "Attempted to put identical "
                    "gauge at key: {key}".format(key=str(key)), level=1)
        else:
            logEvent("Key {key} is already attached.".format(key=str(key)),
                     level=1)

    def attachPointGauges(self, model_key, gauges, activeTime=None,
                          sampleRate=0,
                          fileName='point_gauges.csv'):
        """Attaches Point Gauges (in the Proteus/Gauges.py style) to the shape.

        Parameters
        ----------
        model_key: string
            Label of the model to use as a key for selecting particular gauges.
        See proteus Gauges.py PointGauges class for the remaining parameters.
        """
        new_gauges = Gauges.PointGauges(gauges, activeTime, sampleRate,
                                        fileName)
        self._attachAuxiliaryVariable('Gauge_' + model_key,
                                      gauge=new_gauges)

    def attachLineGauges(self, model_key, gauges, activeTime=None,
                         sampleRate=0,
                         fileName='line_gauges.csv'):
        """Attaches Line Gauges (in the Proteus/Gauges.py style) to the shape.

        Parameters
        ----------
        model_key: string
            Label of the model to use as a key for selecting particular gauges.
        See proteus Gauges.py LineGauges class for the remaining parameters.
        """
        new_gauges = Gauges.LineGauges(gauges, activeTime, sampleRate,
                                       fileName)
        self._attachAuxiliaryVariable('Gauge_' + model_key,
                                      gauge=new_gauges)

    def attachLineIntegralGauges(self, model_key, gauges, activeTime=None,
                                 sampleRate=0,
                                 fileName='line_integral_gauges.csv'):
        """Attaches Line Integral Gauges (in the Proteus/Gauges.py style).

        Parameters
        ----------
        model_key: string
            Label of the model to use as a key for selecting particular gauges.
        See proteus Gauges.py LineIntegralGauges class for the remaining parameters.
        """
        new_gauges = Gauges.LineIntegralGauges(gauges, activeTime,
                                               sampleRate, fileName)
        self._attachAuxiliaryVariable('Gauge_' + model_key,
                                      gauge=new_gauges)


    def setRigidBody(self, holes=None):
        """
        Makes the shape a rigid body

        Parameters
        ----------
        holes: Optional[array_like]
            Used to set coordinates of hole inside the rigid body, so it does
            not get meshed. If not set, the hole coordinates will be the
            barycenter coordinates.
        """
        self._attachAuxiliaryVariable('RigidBody')
        if holes is None:
            self.holes = np.array([self.barycenter[:self.nd]])
        else:
            self._checkListOfLists(holes)
            self.holes = np.array(holes)

    def setTank(self):
        """
        Sets tank boundary conditions (for moving domain).
        """
        for boundcond in self.BC_list:
            boundcond.setTank()

    def setConstraints(self, free_x, free_r):
        """
        Sets constraints on the Shape (for moving bodies)

        Parameters
        ----------
        free_x: array_like
            Translational constraints.
        free_r: array_like
            Rotational constraints.
        """
        self.free_x = np.array(free_x)
        self.free_r = np.array(free_r)

    def setMass(self, mass):
        """
        Set mass of the shape and calculate density if volume is defined.

        Parameters
        ----------
        mass: float
            mass of the body
        """
        self.mass = float(mass)
        if self.volume:
            self.density = self.mass/self.volume

    def setDensity(self, density):
        """
        Set density and calculate mass is volume is defined.

        Parameters
        ----------
        density: float
            Density of the shape
        """
        self.density = float(density)
        if self.volume:
            self.mass = self.density*self.volume

    def _setInertiaTensor(self, It):
        """
        Set the inertia tensor of the shape

        Parameters
        ----------
        It: array_like, float
            Inertia tensor of the body (3x3 array in 3D, float in 2D)

        Notes
        -----
        The inertia tensor should not be already scaled with the mass of the
        shape.
        """
        It = np.array(It)
        if self.nd == 2:
            assert isinstance(It, float), 'the inertia tensor of a 2D shape ' \
                'must be a float'
        if self.nd == 3:
            assert It.shape == (3, 3), 'the inertia tensor of a 3D shape ' \
                'must have a (3, 3) shape'
        self.It = It

    def getInertia(self, vec=(0., 0., 1.), pivot=None):
        """
        Gives the inertia of the shape from an axis and a pivot

        Parameters
        ----------
        vec: array_like
            Vector around which the body rotates.
        pivot: Optional[array_like]
            Pivotal point around which the body rotates. If not set, it will
            be the barycenter coordinates

        Returns
        -------
        I: float
            inertia of the mass

        Notes
        -----
        The inertia is calculated relative to the coordinate system of the
        shape (self.coords_system). If the shape was not initialised with a
        position corresponding to its inertia tensor (e.g. shape was already
        rotated when initialised), set the coordinate system accordingly
        before calling this function
        """
        assert self.It is not None, 'No inertia tensor! (' + self.name + ')'
        if pivot is None:
            pivot = self.barycenter
        # Pivot coords relative to shape centre of mass
        pivot = pivot-np.array(self.barycenter)
        # making unity vector/axis of rotation
        vec = vx, vy, vz = np.array(vec)
        length_vec = sqrt(vx**2+vy**2+vz**2)
        vec = vec/length_vec
        if self.Domain.nd == 2:
            I = self.It*self.mass
        elif self.Domain.nd == 3:
            # vector relative to original position of shape:
            vec = np.dot(vec, np.linalg.inv(self.coords_system))
            cx, cy, cz = vec
            # getting the tensor for calculaing moment of inertia
            # from arbitrary axis
            vt = np.array([[cx**2, cx*cy, cx*cz],
                           [cx*cy, cy**2, cy*cz],
                           [cx*cz, cy*cz, cz**2]])
            # total moment of inertia
            I = np.einsum('ij,ij->', self.mass*self.It, vt)
        return I

    def setRecordValues(self, filename=None, all_values=False, time=True,
                        pos=False, rot=False, F=False, M=False, inertia=False,
                        vel=False, acc=False):
        """
        Sets the rigid body attributes that are to be recorded in a csv file
        during the simulation.

        Parameters
        ----------
        filename: Optional[string]
            Name of file, if not set, the file will be named as follows:
            'record_[shape.name].csv'
        all_values: bool
            Set to True to record all values listed below.
        time: bool
            Time of recorded row (default: True).
        pos: bool
            Position of body (default: False. Set to True to record).
        rot: bool
            Rotation of body (default: False. Set to True to record).
        F: bool
            Forces applied on body (default: False. Set to True to record).
        M: bool
            Moments applied on body (default: False. Set to True to record).
        inertia: bool
            Inertia of body (default: False. Set to True to record).
        vel: bool
            Velocity of body (default: False. Set to True to record).
        acc: bool
            Acceleration of body (default: False. Set to True to record).

        """
        self.record_values = True
        if pos is True:
            x = y = z = True
        if rot is True:
            rot_x = rot_y = rot_z = True
        if F is True:
            Fx = Fy = Fz = True
        if M is True:
            Mx = My = Mz = True
        if vel is True:
            vel_x = vel_y = vel_z = True
        if acc is True:
            acc_x = acc_y = acc_z = True
        self.record_dict = {'time':time, 'pos': pos, 'rot':rot, 'F':F, 'M':M,
                            'inertia': inertia, 'vel': vel, 'acc': acc}
        if all_values is True:
            for key in self.record_dict:
                self.record_dict[key] = True
        if filename is None:
            self.record_filename = 'record_' + self.name + '.csv'
        else:
            self.record_filename = filename + '.csv'

    def setAbsorptionZones(self, flags, epsFact_solid, center, orientation,
                           dragAlpha=0.5/1.005e-6, dragBeta=0.,
                           porosity=1.):
        """
        Sets a region (given the local flag) to an absorption zone

        Parameters
        ----------
        flags: array_like, int
            Local flags of the region. Can be an integer or a list.
        epsFact_solid: float
            Half of absorption zone (region) length (used for blending func).
        center: array_like
            Coordinates of the center of the absorption zone.
        orientation: array_like
            Orientation vector pointing TOWARDS incoming waves.
        dragAlpha: Optional[float]
            Porous module parameter.
        dragBeta: Optional[float]
            Porous module parameter.
        porosity: Optional[float]
            Porous module parameter.
        """
        self._attachAuxiliaryVariable('RelaxZones')
        waves = None
        wind_speed = np.array([0., 0., 0.])
        if isinstance(flags, int):
            flags = [flags]
            epsFact_solid = [epsFact_solid]
            center = np.array([center])
            orientation = np.array([orientation])
            dragAlpha = [dragAlpha]
            dragBeta = [dragBeta]
            porosity = [porosity]
        for i, flag in enumerate(flags):
            self._checkNd(center[i])
            self._checkNd(orientation[i])
            ori = get_unit_vector(orientation[i])
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='absorption',
                                                 orientation=ori,
                                                 center=center[i],
                                                 waves=waves,
                                                 wind_speed=wind_speed,
                                                 epsFact_solid=epsFact_solid[i],
                                                 dragAlpha=dragAlpha[i],
                                                 dragBeta=dragBeta[i],
                                                 porosity=porosity[i])

    def setGenerationZones(self, flags, epsFact_solid, center, orientation,
                           waves, wind_speed=(0., 0., 0.),
                           dragAlpha=0.5/1.005e-6, dragBeta=0.,
                           porosity=1., smoothing=0.):
        """
        Sets a region (given the local flag) to a generation zone

        Parameters
        ----------
        flags: array_like, int
            Local flags of the region. Can be an integer or a list.
        epsFact_solid: float
            Half of absorption zone (region) length (used for blending func).
        center: array_like
            Coordinates of the center of the absorption zone.
        orientation: array_like
            Orientation vector pointing TOWARDS incoming waves.
        waves: proteus.WaveTools
            Class instance of wave generated from proteus.WaveTools.
        wind_speed: Optional[array_like]
            Speed of wind in generation zone (default is (0., 0., 0.))
        dragAlpha: Optional[float]
            Porous module parameter.
        dragBeta: Optional[float]
            Porous module parameter.
        porosity: Optional[float]
            Porous module parameter.
        """
        self._attachAuxiliaryVariable('RelaxZones')
        if isinstance(flags, int):
            flags = [flags]
            epsFact_solid = [epsFact_solid]
            center = np.array([center])
            orientation = np.array([orientation])
            waves = [waves]
            wind_speed = np.array([wind_speed])
            dragAlpha = [dragAlpha]
            dragBeta = [dragBeta]
            porosity = [porosity]
            smoothing = [smoothing]
        for i, flag in enumerate(flags):
            self._checkNd(center[i])
            self._checkNd(orientation[i])
            ori = get_unit_vector(orientation[i])
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='generation',
                                                 orientation=ori,
                                                 center=center[i],
                                                 waves=waves[i],
                                                 wind_speed=wind_speed[i],
                                                 epsFact_solid=epsFact_solid[i],
                                                 dragAlpha=dragAlpha[i],
                                                 dragBeta=dragBeta[i],
                                                 porosity=porosity[i],
                                                 smoothing=smoothing[i])

    def setPorousZones(self, flags, dragAlpha=0.5/1.005e-6, dragBeta=0.,
                       porosity=1.):
        """
        Sets a region (given the local flag) to a porous zone

        Parameters
        ----------
        flags: array_like, int
            Local flags of the region. Can be an integer or a list.
        dragAlpha: Optional[float]
            Porous module parameter.
        dragBeta: Optional[float]
            Porous module parameter.
        porosity: Optional[float]
            Porous module parameter.
        """
        self._attachAuxiliaryVariable('RelaxZones')
        if isinstance(flags, int):
            flags = [flags]
            dragAlpha = [dragAlpha]
            dragBeta = [dragBeta]
            porosity = [porosity]
        for i, flag in enumerate(flags):
            # note for porous zone:
            # epsFact_solid = q_phi_solid, --> Hs always equal to 1.
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='porous',
                                                 orientation=None,
                                                 center=None,
                                                 waves=None,
                                                 wind_speed=None,
                                                 epsFact_solid=1.,
                                                 dragAlpha=dragAlpha[i],
                                                 dragBeta=dragBeta[i],
                                                 porosity=porosity[i])

# -----------------------------------------------------------------------------
# ADDING FUNCTIONALITY TO SHAPE FROM proteus.SpatialTools
# -----------------------------------------------------------------------------

# reassigning base/super class to access all functions from ShapeRANS and Shape
Rectangle.__bases__ = (ShapeRANS,)
Cuboid.__bases__ = (ShapeRANS,)
Sphere.__bases__ = (ShapeRANS,)
CustomShape.__bases__ = (ShapeRANS,)
ShapeSTL.__bases__ = (ShapeRANS,)

# adding extra functionality to predefined shapes

def _CuboidsetInertiaTensor(self):
    """
    Sets the inertia tensor of the cuboid
    (!) should not be used manually
    """
    L, W, H = self.dim
    self.It = [[(W**2.+H**2.)/12., 0, 0],
               [0, (L**2.+H**2.)/12., 0],
               [0, 0, (W**2.+L**2.)/12.]]

Cuboid._setInertiaTensor = _CuboidsetInertiaTensor

def _RectanglesetInertiaTensor(self):
    """
    Sets the inertia tensor of the rectangle
    (!) should not be used manually
    """
    L, H = self.dim
    self.It = (L**2+H**2)/12

Rectangle._setInertiaTensor = _RectanglesetInertiaTensor


# -----------------------------------------------------------------------------
# DEFINING NEW SHAPES TYPES
# -----------------------------------------------------------------------------

class Tank3D(ShapeRANS):
    """
    Class to create a 3D tank (cuboidal shape).

    Parameters
    ----------
    domain: proteus.Domain.D_base
        Domain class instance that hold all the geometrical informations and
        boundary conditions of the shape.
    dim: Optional[array_like]
        Dimensions of the cuboid.
    coords: Optional[array_like]
        Coordinates of the centroid of the shape.
    from_0: Optional[bool]
        If True (default), the tank extends from the origin to postive x, y, z
    """
    count = 0

    def __init__(self, domain, dim=(0., 0., 0.), coords=None, from_0=True):
        super(Tank3D, self).__init__(domain, nd=3)
        self.__class__.count += 1
        self.name = "tank3d" + str(self.__class__.count)
        self.from_0 = from_0
        if coords is None:
            self.coords = np.array(dim)/2.
        else:
            self.coords = coords
            self.from_0 = False
        self.holes = None
        self.boundaryTags = {'z-': 1,
                             'x-': 2,
                             'y+': 3,
                             'x+': 4,
                             'y-': 5,
                             'z+': 6,
                             'sponge': 7}
        self.b_or = np.array([[0.,  0., -1.],
                              [-1., 0.,  0.],
                              [0.,  1.,  0.],
                              [1.,  0.,  0.],
                              [0., -1.,  0.],
                              [0.,  0.,  1.]])
        self.BC = {'z-': self.BC_class(shape=self, name='z-',
                                       b_or=self.b_or, b_i=0),
                   'x-': self.BC_class(shape=self, name='x-',
                                       b_or=self.b_or, b_i=1),
                   'y+': self.BC_class(shape=self, name='y+',
                                       b_or=self.b_or, b_i=2),
                   'x+': self.BC_class(shape=self, name='x+',
                                       b_or=self.b_or, b_i=3),
                   'y-': self.BC_class(shape=self, name='y+',
                                       b_or=self.b_or, b_i=4),
                   'z+': self.BC_class(shape=self, name='z+',
                                       b_or=self.b_or, b_i=5),
                   'sponge': self.BC_class(shape=self, name='sponge')}
        self.BC_list = [self.BC['z-'],
                        self.BC['x-'],
                        self.BC['y+'],
                        self.BC['x+'],
                        self.BC['y+'],
                        self.BC['z+'],
                        self.BC['sponge']]
        # self.BC = BCContainer(self.BC_dict)
        for i in range(6):
            self.BC_list[i].setTank()
        self.barycenter = np.array([0., 0., 0.])
        self.spongeLayers = {'y+': None, 'y-': None, 'x+': None, 'x-': None}
        self.setDimensions(dim)

    def setSponge(self, x_p=None, x_n=None, y_p=None, y_n=None):
        """
        Set length of sponge layers of the tank (used for wave absorption or
        generation zones).
        (!) Sponge layers expand outwards.

        Parameters
        ----------
        x_p: Optional[float]
            length of sponge layer in +x direction.
        x_n: Optional[float]
            length of sponge layer in -x direction.
        y_p: Optional[float]
            length of sponge layer in +y direction.
        y_n: Optional[float]
            length of sponge layer in -y direction.
        """
        self.spongeLayers['x+'] = x_p
        self.spongeLayers['x-'] = x_n
        self.spongeLayers['y+'] = y_p
        self.spongeLayers['y-'] = y_n
        self.setDimensions(self.dim)

    def setDimensions(self, dim):
        """
        Set dimension of the tank

        Parameters
        ----------
        dim: array_like
            dimensions of the tank (excluding sponge layers), array of length 3.
        """
        L, W, H = dim
        self.dim = dim
        if self.from_0 is True:
            x, y, z = L/2., W/2., H/2.
        else:
            x, y, z = self.coords
        self.coords = [x, y, z]
        x0, x1 = x-0.5*L, x+0.5*L
        y0, y1 = y-0.5*W, y+0.5*W
        z0, z1 = z-0.5*H, z+0.5*H
        # ---------------------------------------------
        # first add all vecors, facets, regions at the bottom
        # ---------------------------------------------
        bt = self.boundaryTags
        x_p = self.spongeLayers['x+'] or 0.
        x_n = self.spongeLayers['x-'] or 0.
        y_p = self.spongeLayers['y+'] or 0.
        y_n = self.spongeLayers['y-'] or 0.
        vertices = [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]]
        vertexFlags = [bt['z-'], bt['z-'], bt['z-'], bt['z-']]
        segments = [[0, 1], [1, 2], [2, 3], [3, 0]]
        segmentFlags = [bt['z-'], bt['z-'], bt['z-'], bt['z-']]
        facets = [[[0, 1, 2, 3]]]
        volumes = [[[0]]]
        facetFlags = [bt['z-']]
        regions = [[(x0+x1)/2., (y0+y1)/2., (z0+z1)/2.]]
        regionFlags = [1]
        self.regionIndice = {'tank': 0}
        v_i = 4  # index of next vector to add
        r_i = 1  # index of next region to add
        nb_sponge = 0  # number of sponge layers defined

        if y_n:
            vertices += [[x0, y0-y_n, z0], [x1, y0-y_n, z0]]
            segments += [[0, v_i], [v_i, v_i+1], [v_i+1, 1]]
            facets += [[[0, 1, v_i+1, v_i]]]
            regions += [[(x0+x1)/2., (y0+(y0-y_n))/2., (z0+z1)/2.]]
            self.regionIndice['y-'] = r_i
            regionFlags += [r_i+1]
            v_i += 2  # 2 vertices were added
            r_i += 1  # 1 region was added
            nb_sponge += 1
        if y_p:
            vertices += [[x0, y1+y_p, z0], [x1, y1+y_p, z0]]
            segments += [[3, v_i], [v_i, v_i+1], [v_i+1, 2]]
            facets += [[[3, 2, v_i+1, v_i]]]
            regions += [[(x0+x1)/2., (y1+(y1+y_p))/2., (z0+z1)/2.]]
            self.regionIndice['y+'] = r_i
            regionFlags += [r_i+1]
            v_i += 2
            r_i += 1
            nb_sponge += 1
        if x_p:
            vertices += [[x1+x_p, y0, z0], [x1+x_p, y1, z0]]
            segments += [[1, v_i], [v_i, v_i+1], [v_i+1, 2]]
            facets += [[[1, 2, v_i+1, v_i]]]
            regions += [[(x1+(x1+x_p))/2., (y0+y1)/2., (z0+z1)/2.]]
            self.regionIndice['x+'] = r_i
            regionFlags += [r_i+1]
            v_i += 2
            r_i += 1
            nb_sponge += 1
        if x_n:
            vertices += [[x0-x_n, y0, z0], [x0-x_n, y1, z0]]
            segments += [[0, v_i], [v_i, v_i+1], [v_i+1, 3]]
            facets += [[[0, 3, v_i+1, v_i]]]
            regions += [[(x0+(x0-x_n))/2., (y0+y1)/2., (z0+z1)/2.]]
            self.regionIndice['x-'] = r_i
            regionFlags += [r_i+1]
            v_i += 2
            r_i += 1
            nb_sponge += 1
        # all flags as bottom flags
        for i in range(nb_sponge):
            vertexFlags += [bt['z-'], bt['z-']]
            segmentFlags += [bt['z-'], bt['z-'], bt['z-']]
            facetFlags += [bt['z-']]
            volumes += [[[len(facetFlags)-1]]]
        # ---------------------------------------------
        # Then add the rest of the vectors (top) by symmetry
        # ---------------------------------------------
        # copying list of bottom segments to get top and side segments
        segments_bottom = segments[:]
        # getting top
        vertexFlags += [bt['z+'] for i in range(len(vertices))]
        segmentFlags += [bt['z+'] for i in range(len(segments))]
        facetFlags += [bt['z+'] for i in range(len(facets))]
        vertices_top = np.array(vertices)
        vertices_top[:, 2] = z1
        vertices += vertices_top.tolist()
        segments_top = np.array(segments)
        segments_top += v_i
        segments += segments_top.tolist()
        facets_top = np.array(facets)
        facets_top += v_i
        for vol in volumes:
            vol[0] += [vol[0][0]+len(facetFlags)/2]
        facets += facets_top.tolist()
        # getting sides
        for s in segments_bottom:  # for vertical facets
            facets += [[[s[0], s[1], s[1]+v_i, s[0]+v_i]]]
            if vertices[s[0]][0] == vertices[s[1]][0] == x0:
                if y_n > 0 and (vertices[s[0]][1] == y0-y_n or vertices[s[1]][1] == y0-y_n):
                    volumes[self.regionIndice['y-']][0] += [len(facetFlags)]
                elif y_p > 0 and (vertices[s[0]][1] == y1+y_p or vertices[s[1]][1] == y1+y_p):
                    volumes[self.regionIndice['y+']][0] += [len(facetFlags)]
                else:
                    volumes[self.regionIndice['tank']][0] += [len(facetFlags)]
                if x_n > 0:
                    volumes[self.regionIndice['x-']][0] += [len(facetFlags)]
                    facetFlags += [bt['sponge']]
                else:
                    facetFlags += [bt['x-']]
            elif vertices[s[0]][0] == vertices[s[1]][0] == x0-x_n and x_n > 0:
                volumes[self.regionIndice['x-']][0] += [len(facetFlags)]
                facetFlags += [bt['x-']]
            elif vertices[s[0]][0] == vertices[s[1]][0] == x1:
                if y_n > 0 and (vertices[s[0]][1] == y0-y_n or vertices[s[1]][1] == y0-y_n):
                    volumes[self.regionIndice['y-']][0] += [len(facetFlags)]
                elif y_p > 0 and (vertices[s[0]][1] == y1+y_p or vertices[s[1]][1] == y1+y_p):
                    volumes[self.regionIndice['y+']][0] += [len(facetFlags)]
                else:
                    volumes[self.regionIndice['tank']][0] += [len(facetFlags)]
                if x_p > 0:
                    volumes[self.regionIndice['x+']][0] += [len(facetFlags)]
                    facetFlags += [bt['sponge']]
                else:
                    facetFlags += [bt['x+']]
            elif vertices[s[0]][0] == vertices[s[1]][0] == x1+x_p and x_p > 0:
                volumes[self.regionIndice['x+']][0] += [len(facetFlags)]
                facetFlags += [bt['x+']]
            if vertices[s[0]][1] == vertices[s[1]][1] == y0:
                if x_n > 0 and (vertices[s[0]][0] == x0-x_n or vertices[s[1]][0] == x0-x_n):
                    volumes[self.regionIndice['x-']][0] += [len(facetFlags)]
                elif x_p > 0 and (vertices[s[0]][0] == x1+x_p or vertices[s[1]][0] == x1+x_p):
                    volumes[self.regionIndice['x+']][0] += [len(facetFlags)]
                else:
                    volumes[self.regionIndice['tank']][0] += [len(facetFlags)]
                if y_n > 0:
                    volumes[self.regionIndice['y-']][0] += [len(facetFlags)]
                    facetFlags += [bt['sponge']]
                else:
                    facetFlags += [bt['y-']]
            elif vertices[s[0]][1] == vertices[s[1]][1] == y0-y_n and y_n > 0:
                volumes[self.regionIndice['y-']][0] += [len(facetFlags)]
                facetFlags += [bt['y-']]
            elif vertices[s[0]][1] == vertices[s[1]][1] == y1:
                if x_n > 0 and (vertices[s[0]][0] == x0-x_n or vertices[s[1]][0] == x0-x_n):
                    volumes[self.regionIndice['x-']][0] += [len(facetFlags)]
                elif x_p > 0 and (vertices[s[0]][0] == x1+x_p or vertices[s[1]][0] == x1+x_p):
                    volumes[self.regionIndice['x+']][0] += [len(facetFlags)]
                else:
                    volumes[self.regionIndice['tank']][0] += [len(facetFlags)]
                if y_p > 0:
                    volumes[self.regionIndice['y+']][0] += [len(facetFlags)]
                    facetFlags += [bt['sponge']]
                else:
                    facetFlags += [bt['y+']]
            elif vertices[s[0]][1] == vertices[s[1]][1] == y1+y_p and y_p > 0:
                volumes[self.regionIndice['y+']][0] += [len(facetFlags)]
        # vertical segments
        for i in range(v_i):
            segments += [[i, i+v_i]]
            if vertices[i][0] == vertices[i+v_i][0] == x0-x_n:
                segmentFlags += [bt['x-']]
            elif vertices[i][0] == vertices[i+v_i][0] == x1+x_p:
                segmentFlags += [bt['x+']]
            elif vertices[i][1] == vertices[i+v_i][1] == y0-x_n:
                segmentFlags += [bt['y-']]
            elif vertices[i][1] == vertices[i+v_i][1] == y1+x_p:
                segmentFlags += [bt['y+']]
            else:
                segmentFlags += [bt['sponge']]
        self.vertices = np.array(vertices)
        self.vertices = np.dot(self.vertices, self.coords_system)
        self.vertexFlags = np.array(vertexFlags)
        self.segments = np.array(segments)
        self.segmentFlags = np.array(segmentFlags)
        self.facets = np.array(facets)
        self.facetFlags = np.array(facetFlags)
        self.regions = np.array(regions)
        self.regionFlags = np.array(regionFlags)
        self.volumes = np.array(volumes)


    def setAbsorptionZones(self, allSponge=False, y_n=False, y_p=False,
                           x_n=False, x_p=False, dragAlpha=0.5/1.005e-6,
                           dragBeta=0., porosity=1.):
        """
        Sets regions (x+, x-, y+, y-) to absorption zones

        Parameters
        ----------
        allSponge: bool
            If True, all sponge layers are converted to absorption zones.
        x_p: bool
            If True, x+ region is converted to absorption zone.
        x_n: bool
            If True, x- region is converted to absorption zone.
        y_p: bool
            If True, y+ region is converted to absorption zone.
        y_n: bool
            If True, y- region is converted to absorption zone.
        dragAlpha: Optional[float]
            Porous module parameter.
        dragBeta: Optional[float]
            Porous module parameter.
        porosity: Optional[float]
            Porous module parameter.
        """
        self.abs_zones = {'y-': y_n, 'y+': y_p, 'x-': x_n, 'x+': x_p}
        if allSponge is True:
            for key in self.abs_zones:
                self.abs_zones[key] = True
        waves = None
        wind_speed = np.array([0., 0., 0.])
        sl = self.spongeLayers
        for key, value in self.abs_zones.iteritems():
            if value is True:
                self._attachAuxiliaryVariable('RelaxZones')
                ind = self.regionIndice[key]
                flag = self.regionFlags[ind]
                epsFact_solid = self.spongeLayers[key]/2.
                center = np.array(self.coords)
                zeros_to_append = 3-len(center)
                if zeros_to_append:
                    for i in range(zeros_to_append):
                        center = np.append(center, [0])
                if key == 'x-':
                    center[0] += -0.5*self.dim[0]-0.5*sl['x-']
                    orientation = np.array([1., 0., 0.])
                elif key == 'x+':
                    center[0] += +0.5*self.dim[0]+0.5*sl['x+']
                    orientation = np.array([-1., 0., 0.])
                elif key == 'y-':
                    center[1] += -0.5*self.dim[1]-0.5*sl['y-']
                    orientation = np.array([0., 1., 0.])
                elif key == 'y+':
                    center[1] += +0.5*self.dim[1]+0.5*sl['y+']
                    orientation = np.array([0., -1., 0.])
                self.zones[flag] = bc.RelaxationZone(shape=self,
                                                     zone_type='absorption',
                                                     orientation=orientation,
                                                     center=center,
                                                     waves=waves,
                                                     wind_speed=wind_speed,
                                                     epsFact_solid=epsFact_solid,
                                                     dragAlpha=dragAlpha,
                                                     dragBeta=dragBeta,
                                                     porosity=porosity)

    def setGenerationZones(self, waves=None, wind_speed=(0. ,0., 0.),
                           allSponge=False, y_n=False, y_p=False, x_n=False,
                           x_p=False, dragAlpha=0.5/1.005e-6, dragBeta=0.,
                           porosity=1., smoothing=0.):
        """
        Sets regions (x+, x-, y+, y-) to generation zones

        Parameters
        ----------
        waves: proteus.WaveTools
            Class instance of wave generated from proteus.WaveTools.
        wind_speed: Optional[array_like]
            Speed of wind in generation zone (default is (0., 0., 0.))
        allSponge: bool
            If True, all sponge layers are converted to generation zones.
        x_p: bool
            If True, x+ region is converted to generation zone.
        x_n: bool
            If True, x- region is converted to generation zone.
        y_p: bool
            If True, y+ region is converted to generation zone.
        y_n: bool
            If True, y- region is converted to generation zone.
        dragAlpha: Optional[float]
            Porous module parameter.
        dragBeta: Optional[float]
            Porous module parameter.
        porosity: Optional[float]
            Porous module parameter.
        """
        self.abs_zones = {'y-': y_n, 'y+': y_p, 'x-': x_n, 'x+': x_p}
        if allSponge is True:
            for key in self.abs_zones:
                self.abs_zones[key] = True
        waves = waves
        wind_speed = np.array(wind_speed)
        sl = self.spongeLayers
        for key, value in self.abs_zones.iteritems():
            if value is True:
                self._attachAuxiliaryVariable('RelaxZones')
                ind = self.regionIndice[key]
                flag = self.regionFlags[ind]
                epsFact_solid = self.spongeLayers[key]/2.
                center = np.array(self.coords)
                zeros_to_append = 3-len(center)
                if zeros_to_append:
                    for i in range(zeros_to_append):
                        center = np.append(center, [0])
                if key == 'x-':
                    center[0] += -0.5*self.dim[0]-sl['x-']/2.
                    orientation = np.array([1., 0., 0.])
                    self.BC['x-'].setUnsteadyTwoPhaseVelocityInlet(wave=waves,
                                                                   wind_speed=wind_speed,
                                                                   smoothing=smoothing)
                elif key == 'x+':
                    center[0] += +0.5*self.dim[0]+sl['x+']/2.
                    orientation = np.array([-1., 0., 0.])
                    self.BC['x+'].setUnsteadyTwoPhaseVelocityInlet(wave=waves,
                                                                   wind_speed=wind_speed,
                                                                   smoothing=smoothing)
                elif key == 'y-':
                    center[1] += -0.5*self.dim[1]-sl['y-']/2.
                    orientation = np.array([0., 1., 0.])
                    self.BC['y-'].setUnsteadyTwoPhaseVelocityInlet(wave=waves,
                                                                   wind_speed=wind_speed,
                                                                   smoothing=smoothing)
                elif key == 'y+':
                    center[1] += +0.5*self.dim[1]+sl['y+']/2.
                    orientation = np.array([0., -1., 0.])
                    self.BC['y+'].setUnsteadyTwoPhaseVelocityInlet(wave=waves,
                                                                   wind_speed=wind_speed,
                                                                   smoothing=smoothing)
                self.zones[flag] = bc.RelaxationZone(shape=self,
                                                     zone_type='generation',
                                                     orientation=orientation,
                                                     center=center,
                                                     waves=waves,
                                                     wind_speed=wind_speed,
                                                     epsFact_solid=epsFact_solid,
                                                     dragAlpha=dragAlpha,
                                                     dragBeta=dragBeta,
                                                     porosity=porosity,
                                                     smoothing=smoothing)


class Tank2D(ShapeRANS):
    """
    Class to create a 2D tank (rectangular shape).

    Parameters
    ----------
    domain: proteus.Domain.D_base
        Domain class instance that hold all the geometrical informations and
        boundary conditions of the shape.
    dim: array_like
        Dimensions of the tank (excluding sponge layers).
    coords: Optional[array_like]
        Coordinates of the centroid of the shape.
    from_0: Optional[bool]
        If True (default), the tank extends from the origin to positive x, y, z
    """
    count = 0

    def __init__(self, domain, dim, coords=None, from_0=True):
        super(Tank2D, self).__init__(domain, nd=2)
        self._nameSelf()
        self._setupBCs()
        self.spongeLayers = {'x-': None,
                             'x+': None}
        self._findEdges(dim, coords, from_0)
        self.constructShape()

    def _nameSelf(self):
        self.__class__.count += 1
        self.name = "tank2D" + str(self.__class__.count)

    def _setupBCs(self):
        self.boundaryTags = {'y-': 1, 'x+': 2, 'y+': 3, 'x-': 4, 'sponge': 5}
        self.b_or = np.array([[0., -1., 0.],
                              [1., 0., 0.],
                              [0., 1., 0.],
                              [-1., 0., 0.]])
        self.BC = {'y-': self.BC_class(shape=self, name='y-',
                                       b_or=self.b_or, b_i=0),
                   'x+': self.BC_class(shape=self, name='x+',
                                       b_or=self.b_or, b_i=1),
                   'y+': self.BC_class(shape=self, name='y+',
                                       b_or=self.b_or, b_i=2),
                   'x-': self.BC_class(shape=self, name='x-',
                                       b_or=self.b_or, b_i=3),
                   'sponge': self.BC_class(shape=self, name='sponge')}
        self.BC_list = [self.BC['y-'],
                        self.BC['x+'],
                        self.BC['y+'],
                        self.BC['x-'],
                        self.BC['sponge']]
        # self.BC = BCContainer(self.BC_dict)
        for i in range(4):
            self.BC_list[i].setTank()

    def constructShape(self):
        """
        Construct the geometry of the tank: segments, regions, etc.

        Parameters
        ----------
        frame: array_like
            An array of (x,y) coordinates in counterclockwise order to define
            the boundaries of the main (that is, excluding extensions such as
            sponge zones) shape.  This can be generated with tank2DFrame or
            subclass specific methods.
        frame_flags: array_like
            A corresponding array of boundary tags associated with each point
            in the frame.  This can be generated with tank2DFrame or subclass
            specific methods.
        """
        vertices, vertexFlags = self._constructVertices()
        segments, segmentFlags = self._constructSegments(vertices, vertexFlags)
        regions, regionFlags = self._constructRegions(vertices, vertexFlags,
                                                      segments, segmentFlags)
        facets, facetFlags = self._constructFacets()

        self.vertices     = np.array(vertices)
        self.vertexFlags  = np.array(vertexFlags)
        self.segments     = np.array(segments)
        self.segmentFlags = np.array(segmentFlags)
        self.regions      = np.array(regions)
        self.regionFlags  = np.array(regionFlags)
        self.facets       = np.array(facets)
        self.facetFlags   = np.array(facetFlags)

    def _findEdges(self, dim, coords, from_0):

        if from_0 and (coords == [x * 0.5 for x in dim]):
            coords = None

        if not from_0 and (coords is None):
            raise ValueError("Cannot locate tank center. Either set from_0 = "
                             "True, or pass in center coordinates in [coords]")
        elif from_0 and (coords is not None):
            raise ValueError("The center of the tank cannot be at coords = "
                             + str(coords) + " while also starting from_0  "
                             "(True) with dimensions: " + str(dim))
        elif from_0 and (coords is None):
            self.x0 = 0
            self.x1 = dim[0]
            self.y0 = 0
            self.y1 = dim[1]
        else: # not from_0 and coords is not None
            self.x0 = coords[0] - 0.5 * dim[0]
            self.x1 = coords[0] + 0.5 * dim[0]
            self.y0 = coords[1] - 0.5 * dim[1]
            self.y1 = coords[1] + 0.5 * dim[1]

    def _constructVertices(self):
        vertices = [[self.x0, self.y0],
                    [self.x1, self.y0],
                    [self.x1, self.y1],
                    [self.x0, self.y1]]
        vertexFlags = [self.boundaryTags['y-'],
                       self.boundaryTags['y-'],
                       self.boundaryTags['y+'],
                       self.boundaryTags['y+']]
        if self.spongeLayers['x-']:
            vertices += [[self.x0 - self.spongeLayers['x-'], self.y0],
                         [self.x0 - self.spongeLayers['x-'], self.y1]]
            vertexFlags += [self.boundaryTags['y-'],
                            self.boundaryTags['y+']]
        if self.spongeLayers['x+']:
            vertices += [[self.x1 + self.spongeLayers['x+'], self.y0],
                         [self.x1 + self.spongeLayers['x+'], self.y1]]
            vertexFlags += [self.boundaryTags['y-'],
                            self.boundaryTags['y+']]
        return vertices, vertexFlags

    def _constructSegments(self, vertices, vertexFlags):
        segments = [[0, 1], [1, 2], [2, 3], [3, 0]]
        segmentFlags = [self.boundaryTags['y-'],
                        self.boundaryTags['x+'],
                        self.boundaryTags['y+'],
                        self.boundaryTags['x-']]
        added_vertices = 0
        if self.spongeLayers['x-']:
            segments += [[0, 4 + added_vertices],
                         [4 + added_vertices, 5 + added_vertices],
                         [5 + added_vertices, 3]]
            segmentFlags += [self.boundaryTags['y-'],
                             self.boundaryTags['x-'],
                             self.boundaryTags['y+']]
            segmentFlags[3] = self.boundaryTags['sponge']
            added_vertices += 2
        if self.spongeLayers['x+']:
            segments += [[1, 4 + added_vertices],
                         [4 + added_vertices, 5 + added_vertices],
                         [5 + added_vertices, 2]]
            segmentFlags += [self.boundaryTags['y-'],
                             self.boundaryTags['x+'],
                             self.boundaryTags['y+']]
            segmentFlags[1] = self.boundaryTags['sponge']
            added_vertices += 2
        return segments, segmentFlags

    def _constructFacets(self):
        facets = [[[0, 1, 2, 3]]]
        facetFlags = [1]
        added_vertices = 0
        added_facets = 0
        if self.spongeLayers['x-']:
            facets += [[[3, 0, 4, 5]]]
            facetFlags += [2+added_facets]
            added_vertices += 2
            added_facets += 1
        if self.spongeLayers['x+']:
            facets += [[[2, 1, added_vertices+4, added_vertices+5]]]
            facetFlags += [2+added_facets]
        return facets, facetFlags



    def _constructRegions(self, vertices, vertexFlags, segments, segmentFlags):
        regions = [[self.x0 + 0.01 * (self.x1 - self.x0), 0.5 * (self.y0 + self.y1)],]
        ind_region = 1
        regionFlags = [ind_region,]
        self.regionIndice = {'tank': ind_region - 1}
        if self.spongeLayers['x-']:
            regions += [[self.x0 - 0.5 * self.spongeLayers['x-'],
                         0.5 * (self.y0 + self.y1)]]
            ind_region += 1
            regionFlags += [ind_region]
            self.regionIndice['x-'] = ind_region - 1
        if self.spongeLayers['x+']:
            regions += [[self.x1 + 0.5 * self.spongeLayers['x+'],
                         0.5 * (self.y0 + self.y1)]]
            ind_region += 1
            regionFlags += [ind_region]
            self.regionIndice['x+'] = ind_region - 1
        return regions, regionFlags

    def setSponge(self, x_n=None, x_p=None):
        """
        Set length of sponge layers of the tank (used for wave absorption or
        generation zones).
        (!) Sponge layers expand outwards.

        Parameters
        ----------
        x_p: Optional[float]
            length of sponge layer in +x direction.
        x_n: Optional[float]
            length of sponge layer in -x direction.
        """
        self.spongeLayers['x-'] = x_n
        self.spongeLayers['x+'] = x_p
        self.constructShape()

    def setAbsorptionZones(self, x_n=False, x_p=False, dragAlpha=0.5/1.005e-6,
                           dragBeta=0., porosity=1.):
        """
        Sets regions (x+, x-) to absorption zones

        Parameters
        ----------
        allSponge: bool
            If True, all sponge layers are converted to absorption zones.
        x_p: bool
            If True, x+ region is converted to absorption zone.
        x_n: bool
            If True, x- region is converted to absorption zone.
        dragAlpha: Optional[float]
            Porous module parameter.
        dragBeta: Optional[float]
            Porous module parameter.
        porosity: Optional[float]
            Porous module parameter.
        """
        waves = None
        wind_speed = np.array([0., 0., 0.])
        if x_n or x_p:
            self._attachAuxiliaryVariable('RelaxZones')
        if x_n is True:
            center = np.array([self.x0 - 0.5 * self.spongeLayers['x-'],
                               0.5 * (self.y0 + self.y1), 0.])
            ind = self.regionIndice['x-']
            flag = self.regionFlags[ind]
            epsFact_solid = self.spongeLayers['x-']/2.
            orientation = np.array([1., 0.])
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='absorption',
                                                 orientation=orientation,
                                                 center=center,
                                                 waves=waves,
                                                 wind_speed=wind_speed,
                                                 epsFact_solid=epsFact_solid,
                                                 dragAlpha=dragAlpha,
                                                 dragBeta=dragBeta,
                                                 porosity=porosity)
        if x_p is True:
            center = np.array([self.x1 + 0.5 * self.spongeLayers['x+'],
                               0.5 * (self.y0 + self.y1), 0.])
            ind = self.regionIndice['x+']
            flag = self.regionFlags[ind]
            epsFact_solid = self.spongeLayers['x+']/2.
            orientation = np.array([-1., 0.])
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='absorption',
                                                 orientation=orientation,
                                                 center=center,
                                                 waves=waves,
                                                 wind_speed=wind_speed,
                                                 epsFact_solid=epsFact_solid,
                                                 dragAlpha=dragAlpha,
                                                 dragBeta=dragBeta,
                                                 porosity=porosity)

    def setGenerationZones(self, waves=None, wind_speed=(0., 0., 0.),
                           x_n=False, x_p=False,  dragAlpha=0.5/1.005e-6,
                           dragBeta=0., porosity=1., smoothing=0.):
        """
        Sets regions (x+, x-) to generation zones

        Parameters
        ----------
        waves: proteus.WaveTools
            Class instance of wave generated from proteus.WaveTools.
        wind_speed: Optional[array_like]
            Speed of wind in generation zone (default is (0., 0., 0.))
        allSponge: bool
            If True, all sponge layers are converted to generation zones.
        x_p: bool
            If True, x+ region is converted to generation zone.
        x_n: bool
            If True, x- region is converted to generation zone.
        dragAlpha: Optional[float]
            Porous module parameter.
        dragBeta: Optional[float]
            Porous module parameter.
        porosity: Optional[float]
            Porous module parameter.
        """
        waves = waves
        wind_speed = np.array(wind_speed)
        if x_n or x_p:
            self._attachAuxiliaryVariable('RelaxZones')
        if x_n is True:
            center = np.array([self.x0 - 0.5 * self.spongeLayers['x-'],
                               0.5 * (self.y0 + self.y1), 0.])
            ind = self.regionIndice['x-']
            flag = self.regionFlags[ind]
            epsFact_solid = self.spongeLayers['x-']/2.
            orientation = np.array([1., 0.])
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='generation',
                                                 orientation=orientation,
                                                 center=center,
                                                 waves=waves,
                                                 wind_speed=wind_speed,
                                                 epsFact_solid=epsFact_solid,
                                                 dragAlpha=dragAlpha,
                                                 dragBeta=dragBeta,
                                                 porosity=porosity,
                                                 smoothing=smoothing)
            self.BC['x-'].setUnsteadyTwoPhaseVelocityInlet(wave=waves,
                                                           wind_speed=wind_speed,
                                                           smoothing=smoothing)
        if x_p is True:
            center = np.array([self.x1 + 0.5 * self.spongeLayers['x+'],
                               0.5 * (self.y0 + self.y1), 0.])
            ind = self.regionIndice['x+']
            flag = self.regionFlags[ind]
            epsFact_solid = self.spongeLayers['x+']/2.
            orientation = np.array([-1., 0.])
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='generation',
                                                 orientation=orientation,
                                                 center=center,
                                                 waves=waves,
                                                 wind_speed=wind_speed,
                                                 epsFact_solid=epsFact_solid,
                                                 dragAlpha=dragAlpha,
                                                 dragBeta=dragBeta,
                                                 porosity=porosity,
                                                 smoothing=smoothing)
            self.BC['x+'].setUnsteadyTwoPhaseVelocityInlet(wave=waves,
                                                           wind_speed=wind_speed,
                                                           smoothing=smoothing)

#[temp] no tests yet!
class TankWithObstacles2D(Tank2D):
    """
    Class to create a 2D rectangular tank with obstacles built out of any wall.

    An obstacle is defined by a contiguous list of points which start and end
    on the walls or corners of the tank.

    This also covers special boundary conditions.  To tag a segment with a
    unique boundary tag, add the starting vertex (in the counterclockwise
    format the shape is generated in) of the segment as a value in a dictionary
    element keyed to the name of the boundary tag.

    (!) Warning: If each of the four corners of the rectangular tank is inside
    an obstacle or a vertex for an obstacle, then the tank's region is defined
    in a pseudorandom manner, which may make it unreliable when dealing with
    holes caused by other shapes.
    (!) Warning: Obstacle boundary tags are keyed to whichever edge they started
    from.  If this is undesirable, it may be manually overriden by applying
    special boundary conditions to affected segments.

    Parameters
    ----------
    domain: proteus.Domain.D_base
        Domain class instance that hold all the geometrical informations and
        boundary conditions of the shape.
    dim: Optional[array_like]
        Dimensions of the cuboid.
    obstacles: Optional[array_like]
        A list of lists of (x,y) coordinates.  Each (x,y) coordinate is a length
        and height relative to the x-,y- corner of the tank.  Each list of
        coordinates is an obstacle defined by points connected in the order of
        their index.  The list of lists gives all such obstacles in a
        counterclockwise manner of which they should be appended, starting from
        the (x-,y-) corner.
    special_boundaries: Optional[mapping]
        A dictionary of lists of vertices keyed to boundary names. The vertices
        listed will be given the boundary name they are keyed to, overriding
        any other designation they would be given.
        If this is a distinct boundary name, the segment starting from the vertex
        will be assigned the same boundary tag.
    full_circle: Optional[bool]
        A boolean tag to check if the final obstacle ends on the same edge that
        the first obstacle starts on.  Default is False.
    coords: Optional[array_like]
        Coordinates of the centroid of the shape.
    from_0: Optional[bool]
        If True (default), the tank extends from the origin to positive x, y, z
    """
    def __init__(self, domain, dim=(0., 0.),
                 obstacles = None, special_boundaries = None,
                 full_circle = False,
                 coords=None, from_0=True):
        if obstacles:
            self.obstacles = obstacles
        else:
            self.obstacles = []

        self.special_boundaries = []
        self.special_BC_vertices = []
        self.full_circle = full_circle

        self.spongeLayers = {'x-': None,
                             'x+': None}

        if special_boundaries:
            for key in special_boundaries.keys():
                self.special_boundaries += [key for v in special_boundaries[key]]
                self.special_BC_vertices += special_boundaries[key]

        self.corners = {'x-y-': False, 'x+y-': False,
                        'x+y+': False, 'x-y+': False}

        super(TankWithObstacles2D, self).__init__(domain, dim, coords, from_0)

    def _setupBCs(self):
        super(TankWithObstacles2D, self)._setupBCs()
        for boundary in self.special_boundaries:
            if boundary not in self.boundaryTags.keys():
                self.boundaryTags[boundary] = len(self.boundaryTags) + 1
                self.BC[boundary] = self.BC_class(shape=self, name=boundary)
                self.BC_list += [self.BC[boundary]]

    def _resetEdgesFromVertices(self, vertices):
        """
        Resets self.x0, self.x1, self.y0, self.y1 based on the actual shape.

        In particular, they will form a bounding box form around the shape -
        the furthest points in x and y dimensions, both high and low.

        Parameters
        ----------
        vertices: array_like
        """
        sorted_vertices = sorted(vertices, key=lambda vertex: vertex[1])
        self.y0 = sorted_vertices[0][1]
        self.y1 = sorted_vertices[-1][1]
        sorted_vertices = sorted(vertices, key=lambda vertex: vertex[0])
        self.x0 = sorted_vertices[0][0]
        self.x1 = sorted_vertices[-1][0]

    def _findSpongeLayerCorners(self, vertices):
        """
        Finds the corners for horizontal (x-, x+) sponge layers.

        Parameters
        ----------
        vertices: array_like
        """
        self._resetEdgesFromVertices(vertices)

        potential_x_n_corners = [vertex for vertex in vertices
                                 if np.isclose(vertex[0], self.x0)]
        potential_x_p_corners = [vertex for vertex in vertices
                                 if np.isclose(vertex[0], self.x1)]

        potential_x_n_corners.sort(key=lambda vertex: vertex[1])
        potential_x_p_corners.sort(key=lambda vertex: vertex[1])

        self.x0y0 = potential_x_n_corners[0]
        self.x0y1 = potential_x_n_corners[-1]
        self.x1y0 = potential_x_p_corners[0]
        self.x1y1 = potential_x_p_corners[-1]

    def _constructVertices(self):

        def getClockwiseOrder(first_point):
            clockwise_ordering = ('x-y-', 'y-', 'x+y-', 'x+',
                                  'x+y+', 'y+', 'x-y+', 'x-')
            index = clockwise_ordering.index(first_point)
            return clockwise_ordering[index:] + clockwise_ordering[:index]

        def findLocation(vertex):
            """
            Given an (x,y) coordinate gives a label associated to corner or edge
            """
            dim = [self.x1 - self.x0, self.y1 - self.y0]
            if np.isclose(vertex[0],0) and np.isclose(vertex[1],0):
                return 'x-y-'
            elif np.isclose(vertex[0],dim[0]) and np.isclose(vertex[1],dim[1]):
                return 'x+y+'
            elif np.isclose(vertex[0],0) and np.isclose(vertex[1],dim[1]):
                return 'x-y+'
            elif np.isclose(vertex[0],dim[0]) and np.isclose(vertex[1],0):
                return 'x+y-'
            elif np.isclose(vertex[0],0):
                return 'x-'
            elif np.isclose(vertex[0],dim[0]):
                return 'x+'
            elif np.isclose(vertex[1],0):
                return 'y-'
            elif np.isclose(vertex[1],dim[1]):
                return 'y+'
            else:
                raise ValueError("Point " + str(vertex) + " does not seem to"
                                 "be on a tank wall.")

        def checkClosure(start_point, end_point):
            if start_point == end_point:
                return True

        def addCorner(corner_flag):
            if corner_flag == 'x-y-':
                corner = [[self.x0, self.y0]]
            elif corner_flag == 'x+y-':
                corner = [[self.x1, self.y0]]
            elif corner_flag == 'x+y+':
                corner = [[self.x1, self.y1]]
            elif corner_flag == 'x-y+':
                corner = [[self.x0, self.y1]]
            # vertex flags
            if corner_flag in ['x-y-', 'x+y-']:
                corner_tag = [self.boundaryTags['y-']]
            else:
                corner_tag = [self.boundaryTags['y+']]

            return corner, corner_tag

        def addIntermediateCorners(first, last):
            """
            Returns corner vertices (and flags) in between two segments
            """
            ordering = getClockwiseOrder(first)
            corners = [x for x in ordering
                       if x in self.corners.keys()
                       and ordering.index(x) < ordering.index(last)
                       ]
            corner_vertices = []
            corner_flags = []

            for corner in corners:
                self.corners[corner] = True
                vertex, flag = addCorner(corner)
                corner_vertices += vertex
                corner_flags += flag

            return corner_vertices, corner_flags

        def addRemainingCorners(first, last):
            if first == last:
                if self.full_circle:
                    return []
                else:
                    return addAllCorners(first)
            else:
                return addIntermediateCorners(first, last)

        def addAllCorners(starting_point):
            """
            Returns all corners and flags.
            """
            corner_vertices = []
            corner_flags = []

            ordering = getClockwiseOrder(starting_point)

            for potential_corner in ordering:
                if potential_corner in self.corners.keys():
                    self.corners[potential_corner] = True
                    vertex, flag = addCorner(potential_corner)
                    corner_vertices += vertex
                    corner_flags += flag

            return corner_vertices, corner_flags

        def addSpongeVertices():
            sponge_vertices = []
            sponge_vertexFlags = []
            if self.spongeLayers['x-']:
                sponge_vertices += [[v[0] - self.spongeLayers['x-'], v[1]]
                                    for v in [self.x0y0, self.x0y1]]
                sponge_vertexFlags += [self.boundaryTags['y-'],
                                       self.boundaryTags['y+']]
            if self.spongeLayers['x+']:
                sponge_vertices += [[v[0] + self.spongeLayers['x+'], v[1]]
                                    for v in [self.x1y0, self.x1y1]]
                sponge_vertexFlags += [self.boundaryTags['y-'],
                                       self.boundaryTags['y+']]
            return sponge_vertices, sponge_vertexFlags

        #--------------------------------------------------------#
        vertices = []
        vertexFlags = []
        former_end = None
        first_start = None

        for obstacle in self.obstacles:
            start = findLocation(obstacle[0])
            end = findLocation(obstacle[-1])

            if start == end and checkClosure(obstacle[0],obstacle[-1]):
                raise ValueError("Obstacles must be open (start and end"
                                 " vertices must be distinct)")
            if start == former_end and checkClosure(obstacle[0], vertices[-1]):
                vertices.pop()
                vertexFlags.pop()

            # ---- In-Between Corner Vertices ---- #
            if former_end is not None:
                new_vertices, new_flags = addIntermediateCorners(former_end, start)
                vertices += new_vertices
                vertexFlags += new_flags

            # ---- Obstacle ---- #
            vertices += obstacle
            vertexFlags += [self.boundaryTags[start]
                            for i in range(len(obstacle))]

            # ---- Paperwork ---- #
            former_end = end
            if first_start is None:
                first_start = start

        # ---- Remaining Corner Vertices ---- #
        if first_start is not None:
            new_vertices, new_flags = addRemainingCorners(former_end,
                                                          first_start)
        else:
            new_vertices, new_flags = addAllCorners('x-')

        vertices += new_vertices
        vertexFlags += new_flags

        # ---- Check for Special Conditions ---- #
        for vertex in self.special_BC_vertices:
            flag_index = vertices.index(vertex)
            boundary_index = self.special_BC_vertices.index(vertex)
            boundary_name = self.special_boundaries[boundary_index]
            vertexFlags[flag_index] = self.boundaryTags[boundary_name]

        # ---- Adjustments for Sponge Zones ---- #
        self._findSpongeLayerCorners(vertices=vertices)

        # ---- Add Sponge Zone Vertices ---- #
        new_vertices, new_flags = addSpongeVertices()
        vertices += new_vertices
        vertexFlags += new_flags

        return vertices, vertexFlags

    def _constructSegments(self, vertices, vertexFlags):
        # VertexFlag --> SegmentFlag logic:
        #
        # if EITHER are x+  --> segment is x+
        #                       UNLESS the other is x-  --> y+
        # if EITHER are x-  --> segment is x-
        #                       UNLESS the other is x+  --> y-
        # if it STARTS y-   --> segment is y-
        #                       UNLESS they are vertical --> x+
        # if it STARTS y+   --> segment is y+
        #                       UNLESS they are vertical --> x-
        # if BOTH are ***   --> segment is ***
        # (if two different *** are around, it takes the first)
        segments = []
        segmentFlags = []

        on_sponge_edge = {'x-': False, 'x+': False}
        sponge_edges_covered = {'x-': False, 'x+': False}

        def checkSpongeStatus(start_index, end_index):
            start_vertex = vertices[start_index]
            if self.spongeLayers['x-']:
                if not on_sponge_edge['x-']:
                    if start_vertex in (self.x0y0, self.x0y1):
                        on_sponge_edge['x-'] = True
                elif not sponge_edges_covered['x-']:
                    if start_vertex in (self.x0y0, self.x0y1):
                        on_sponge_edge['x-'] = False
                        sponge_edges_covered['x-'] = True
                    else:
                        vertexFlags[start_index] = self.boundaryTags['sponge']
                else:
                    pass

            if self.spongeLayers['x+']:
                if not on_sponge_edge['x+']:
                    if start_vertex in (self.x1y0, self.x1y1):
                        on_sponge_edge['x+'] = True
                elif not sponge_edges_covered['x+']:
                    if start_vertex in (self.x1y0, self.x1y1):
                        on_sponge_edge['x+'] = False
                        sponge_edges_covered['x+'] = True
                    else:
                        vertexFlags[start_index] = self.boundaryTags['sponge']
                else:
                    pass

            end_vertex = vertices[end_index]
            if on_sponge_edge['x-']:
                if end_vertex not in (self.x0y0, self.x0y1):
                    vertexFlags[end_index] = self.boundaryTags['sponge']
            if on_sponge_edge['x+']:
                if end_vertex not in (self.x1y0, self.x1y1):
                    vertexFlags[end_index] = self.boundaryTags['sponge']


        def getSegmentFlag(start, end):
            if ((self.spongeLayers['x-'] and not sponge_edges_covered['x-']) or
                (self.spongeLayers['x+'] and not sponge_edges_covered['x+'])):
                checkSpongeStatus(start, end)

            if on_sponge_edge['x-'] or on_sponge_edge['x+']:
                return [self.boundaryTags['sponge'], ]

            else:
                if vertexFlags[start] == self.boundaryTags['x+']:
                    if vertexFlags[end] == self.boundaryTags['x-']:
                        return [self.boundaryTags['y+'], ]
                    else:
                        return [self.boundaryTags['x+'], ]

                elif vertexFlags[start] == self.boundaryTags['x-']:
                    if vertexFlags[end] == self.boundaryTags['x+']:
                        return [self.boundaryTags['y-'], ]
                    else:
                        return [self.boundaryTags['x-'], ]

                elif vertexFlags[end] == self.boundaryTags['x+']:
                    if vertexFlags[start] in [self.boundaryTags['y-'],
                                              self.boundaryTags['y+']]:
                        return [self.boundaryTags['x+'], ]

                elif vertexFlags[end] == self.boundaryTags['x-']:
                    if vertexFlags[start] in [self.boundaryTags['y-'],
                                              self.boundaryTags['y+']]:
                        return [self.boundaryTags['x-'], ]

                elif vertexFlags[start] == self.boundaryTags['y-']:
                    if (vertexFlags[end] == self.boundaryTags['y+']
                        and np.isclose(vertices[start][0], vertices[end][0])
                        ):
                        return [self.boundaryTags['x+'], ]
                    else:
                        return [self.boundaryTags['y-'], ]

                elif vertexFlags[start] == self.boundaryTags['y+']:
                    if (vertexFlags[end] == self.boundaryTags['y-']
                        and np.isclose(vertices[start][0], vertices[end][0])
                        ):
                        return [self.boundaryTags['x-'], ]
                    else:
                        return [self.boundaryTags['y+'], ]

                else:
                    return [vertexFlags[start], ]

        # ---- Initial Sponge Logic ---- #
        sponge_vertex_count = 0

        if self.spongeLayers['x-']:
            sponge_vertex_count += 2
        if self.spongeLayers['x+']:
            sponge_vertex_count += 2

        # ---- Build Main Segments ---- #
        for i in range(len(vertices) - 1 - sponge_vertex_count):
            segments += [[i, i + 1], ]
            segmentFlags += getSegmentFlag(i, i + 1)
        segments += [[len(vertices) - 1 - sponge_vertex_count, 0], ]
        segmentFlags += getSegmentFlag(len(vertices) - 1 - sponge_vertex_count,
                                       0)

        # ---- Build Sponge Segments ---- #
        if self.spongeLayers['x-']:
            segments += [[vertices.index(self.x0y0),
                          len(vertices) - sponge_vertex_count],
                         [len(vertices) - sponge_vertex_count,
                          len(vertices) - sponge_vertex_count + 1],
                         [len(vertices) - sponge_vertex_count + 1,
                          vertices.index(self.x0y1)]
                         ]
            segmentFlags += [self.boundaryTags['y-'],
                             self.boundaryTags['x-'],
                             self.boundaryTags['y+']]
        if self.spongeLayers['x+']:
            segments += [[vertices.index(self.x1y0), len(vertices) - 2],
                         [len(vertices) - 2, len(vertices) - 1],
                         [len(vertices) - 1, vertices.index(self.x1y1)]
                         ]
            segmentFlags += [self.boundaryTags['y-'],
                             self.boundaryTags['x+'],
                             self.boundaryTags['y+']]

        return segments, segmentFlags

    def _constructRegions(self, vertices, vertexFlags, segments, segmentFlags):
        if True in self.corners.values():
            regions = self._getCornerRegion()
        else:
            regions = self._getRandomRegion(vertices, segments)

        ind_region = 1
        regionFlags = [ind_region,]
        self.regionIndice = {'tank': ind_region - 1}

        sponge_half_height_x0 = 0.5 * (self.x0y0[1] + self.x0y1[1])
        sponge_half_height_x1 = 0.5 * (self.x1y0[1] + self.x1y1[1])
        sponge_x0 = self.x0y0[0]
        sponge_x1 = self.x1y0[0]

        if self.spongeLayers['x-']:
            regions += [[sponge_x0 - 0.5 * self.spongeLayers['x-'],
                         sponge_half_height_x0]]
            ind_region += 1
            regionFlags += [ind_region]
            self.regionIndice['x-'] = ind_region - 1
        if self.spongeLayers['x+']:
            regions += [[sponge_x1 + 0.5 * self.spongeLayers['x+'],
                         sponge_half_height_x1]]
            ind_region += 1
            regionFlags += [ind_region]
            self.regionIndice['x+'] = ind_region - 1

        return regions, regionFlags

    def _findExtrema(self, points):
        """
        Return the extrema of a series of points in n dimensions in the form:
        max(x1), max(x2), ... , max(xn), min(x1), ... , min(xn)
        """
        points = np.array(points)
        return np.max(points,0).tolist() + np.min(points,0).tolist()

    def _getCornerRegion(self):
        eps = np.finfo(float).eps
        if self.corners['x-y-']:
            return [[self.x0 + eps, self.y0 + eps], ]
        elif self.corners['x+y-']:
            return [[self.x1 - eps, self.y0 + eps], ]
        elif self.corners['x+y+']:
            return [[self.x1 - eps, self.y1 - eps], ]
        elif self.corners['x-y+']:
            return [[self.x0 + eps, self.y1 - eps], ]

    def _getRandomRegion(self, vertices, segments):
        x_p, y_p, x_n, y_n = self._findExtrema(vertices)
        if self.spongeLayers['x-']:
            x_n += self.spongeLayers['x-']
        if self.spongeLayers['x+']:
            x_p -= self.spongeLayers['x+']

        count = 0
        allowed_tries = 100

        while True:
            count += 1
            vertical_line = np.random.uniform(x_n, x_p)
            if True in [np.isclose(vertical_line, vertex[0]) for vertex in
                        vertices]:
                continue

            lowest_intersect = second_intersect = y_p

            for segment in segments:
                line_x0 = vertices[segment[0]][0]
                line_y0 = vertices[segment[0]][1]
                line_x1 = vertices[segment[1]][0]
                line_y1 = vertices[segment[1]][1]
                if (line_x0 < vertical_line < line_x1
                    or line_x0 > vertical_line > line_x1):
                    # (due to the strict inequality check and
                    # our selection of vertical_line - x1 > x0 should be sure)
                    intersection_height = line_y0 + (
                        (line_y1 - line_y0)
                        * (vertical_line - line_x0)
                        / (line_x1 - line_x0)
                    )
                    if intersection_height < lowest_intersect:
                        second_intersect = lowest_intersect
                        lowest_intersect = intersection_height
                    elif intersection_height < second_intersect:
                        second_intersect = intersection_height

            interior_point = 0.5 * (lowest_intersect + second_intersect)

            if lowest_intersect < interior_point < second_intersect:
                break
            if count > allowed_tries:
                ValueError(
                    "Cannot find a proper interior point of the defined "
                    "shape after " + str(count) + " tries.")

        return [[vertical_line, interior_point], ]

    def setAbsorptionZones(self, x_n=False, x_p=False, dragAlpha=0.5/1.005e-6,
                           dragBeta=0., porosity=1.):
        """
        Sets regions (x+, x-) to absorption zones

        Parameters
        ----------
        allSponge: bool
            If True, all sponge layers are converted to absorption zones.
        x_p: bool
            If True, x+ region is converted to absorption zone.
        x_n: bool
            If True, x- region is converted to absorption zone.
        dragAlpha: Optional[float]
            Porous module parameter.
        dragBeta: Optional[float]
            Porous module parameter.
        porosity: Optional[float]
            Porous module parameter.
        """
        sponge_half_height_x0 = 0.5 * (self.x0y0[1] + self.x0y1[1])
        sponge_half_height_x1 = 0.5 * (self.x1y0[1] + self.x1y1[1])
        sponge_x0 = self.x0y0[0]
        sponge_x1 = self.x1y0[0]

        waves = None
        wind_speed = np.array([0., 0., 0.])
        if x_n or x_p:
            self._attachAuxiliaryVariable('RelaxZones')
        if x_n is True:
            center = np.array([sponge_x0 - 0.5 * self.spongeLayers['x-'],
                               sponge_half_height_x0, 0.])
            ind = self.regionIndice['x-']
            flag = self.regionFlags[ind]
            epsFact_solid = self.spongeLayers['x-']/2.
            orientation = np.array([1., 0.])
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='absorption',
                                                 orientation=orientation,
                                                 center=center,
                                                 waves=waves,
                                                 wind_speed=wind_speed,
                                                 epsFact_solid=epsFact_solid,
                                                 dragAlpha=dragAlpha,
                                                 dragBeta=dragBeta,
                                                 porosity=porosity)
        if x_p is True:
            center = np.array([sponge_x1 + 0.5 * self.spongeLayers['x+'],
                               sponge_half_height_x1, 0.])
            ind = self.regionIndice['x+']
            flag = self.regionFlags[ind]
            epsFact_solid = self.spongeLayers['x+']/2.
            orientation = np.array([-1., 0.])
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='absorption',
                                                 orientation=orientation,
                                                 center=center,
                                                 waves=waves,
                                                 wind_speed=wind_speed,
                                                 epsFact_solid=epsFact_solid,
                                                 dragAlpha=dragAlpha,
                                                 dragBeta=dragBeta,
                                                 porosity=porosity)

    def setGenerationZones(self, waves=None, wind_speed=(0., 0., 0.),
                           x_n=False, x_p=False,  dragAlpha=0.5/1.005e-6,
                           dragBeta=0., porosity=1., smoothing=0.):
        """
        Sets regions (x+, x-) to generation zones

        Parameters
        ----------
        waves: proteus.WaveTools
            Class instance of wave generated from proteus.WaveTools.
        wind_speed: Optional[array_like]
            Speed of wind in generation zone (default is (0., 0., 0.))
        allSponge: bool
            If True, all sponge layers are converted to generation zones.
        x_p: bool
            If True, x+ region is converted to generation zone.
        x_n: bool
            If True, x- region is converted to generation zone.
        dragAlpha: Optional[float]
            Porous module parameter.
        dragBeta: Optional[float]
            Porous module parameter.
        porosity: Optional[float]
            Porous module parameter.
        """
        sponge_half_height_x0 = 0.5 * (self.x0y0[1] + self.x0y1[1])
        sponge_half_height_x1 = 0.5 * (self.x1y0[1] + self.x1y1[1])
        sponge_x0 = self.x0y0[0]
        sponge_x1 = self.x1y0[0]

        waves = waves
        wind_speed = np.array(wind_speed)
        if x_n or x_p:
            self._attachAuxiliaryVariable('RelaxZones')
        if x_n is True:

            center = np.array([sponge_x0 - 0.5 * self.spongeLayers['x-'],
                               sponge_half_height_x0, 0.])
            ind = self.regionIndice['x-']
            flag = self.regionFlags[ind]
            epsFact_solid = self.spongeLayers['x-']/2.
            orientation = np.array([1., 0.])
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='generation',
                                                 orientation=orientation,
                                                 center=center,
                                                 waves=waves,
                                                 wind_speed=wind_speed,
                                                 epsFact_solid=epsFact_solid,
                                                 dragAlpha=dragAlpha,
                                                 dragBeta=dragBeta,
                                                 porosity=porosity,
                                                 smoothing=smoothing)
            self.BC['x-'].setUnsteadyTwoPhaseVelocityInlet(wave=waves,
                                                           wind_speed=wind_speed,
                                                           smoothing=smoothing)
        if x_p is True:

            center = np.array([sponge_x1 + 0.5 * self.spongeLayers['x+'],
                               sponge_half_height_x1, 0.])
            ind = self.regionIndice['x+']
            flag = self.regionFlags[ind]
            epsFact_solid = self.spongeLayers['x+']/2.
            orientation = np.array([-1., 0.])
            self.zones[flag] = bc.RelaxationZone(shape=self,
                                                 zone_type='generation',
                                                 orientation=orientation,
                                                 center=center,
                                                 waves=waves,
                                                 wind_speed=wind_speed,
                                                 epsFact_solid=epsFact_solid,
                                                 dragAlpha=dragAlpha,
                                                 dragBeta=dragBeta,
                                                 porosity=porosity,
                                                 smoothing=smoothing)
            self.BC['x+'].setUnsteadyTwoPhaseVelocityInlet(wave=waves,
                                                           wind_speed=wind_speed,
                                                           smoothing=smoothing)

class RigidBody(AuxiliaryVariables.AV_base):
    """
    Auxiliary variable used to calculate attributes of an associated shape
    class instance acting as a rigid body. To set a shape as a rigid body, use
    shape.setRigidBody(). The class instance is created automatically when
    shape.setRigidBody() has been called and after calling assembleDomain().

    Parameters
    ----------
    shape: proteus.mprans.SpatialTools.Shape_RANS
        Class instance of the shape associated to the rigid body calculations.
    cfl_target: Optional[float]
        UNUSED (to implement), sets the maximum displacement of the body
        allowed per time step.
    dt_init: float
        first time step of the simulation.
    """

    def __init__(self, shape, cfl_target=0.9, dt_init=0.001):
        self.Shape = shape
        # if isinstance(shape, (Rectangle, Cuboid)):
        #     shape._setInertiaTensor()
        self.dt_init = dt_init
        self.cfl_target = 0.9
        self.last_position = np.array([0., 0., 0.])
        self.rotation_matrix = np.eye(3)
        self.h = np.array([0., 0., 0.])
        self.barycenter = np.zeros(3)
        self.i_start = None  # will be retrieved from setValues() of Domain
        self.i_end = None  # will be retrieved from setValues() of Domain

    def attachModel(self, model, ar):
        """
        Attaches model to auxiliary variable
        """
        self.model = model
        self.ar = ar
        self.writer = Archiver.XdmfWriter()
        self.nd = model.levelModelList[-1].nSpace_global
        m = self.model.levelModelList[-1]
        flagMax = max(m.mesh.elementBoundaryMaterialTypes)
        # flagMin = min(m.mesh.elementBoundaryMaterialTypes)
        self.nForces = flagMax+1
        return self

    def calculate_init(self):
        """
        Function called at the very beginning of the simulation by proteus.
        """
        nd = self.Shape.Domain.nd
        shape = self.Shape
        self.position = np.zeros(3)
        self.position[:] = self.Shape.barycenter.copy()
        self.last_position[:] = self.position
        self.velocity = np.zeros(3, 'd')
        self.last_velocity = np.zeros(3, 'd')
        self.acceleration = np.zeros(3, 'd')
        self.last_acceleration = np.zeros(3, 'd')
        self.rotation = np.eye(3)
        self.rotation[:nd, :nd] = shape.coords_system
        self.last_rotation = np.eye(3)
        self.last_rotation[:nd, :nd] = shape.coords_system
        self.F = np.zeros(3, 'd')
        self.M = np.zeros(3, 'd')
        self.last_F = np.zeros(3, 'd')
        self.last_M = np.zeros(3, 'd')
        self.ang = 0.
        self.barycenter = self.Shape.barycenter
        self.angvel = np.zeros(3, 'd')
        self.last_angvel = np.zeros(3, 'd')
        if nd == 2:
            self.Fg = self.Shape.mass*np.array([0., -9.81, 0.])
        if nd == 3:
            self.Fg = self.Shape.mass*np.array([0., 0., -9.81])
        if self.Shape.record_values is True:
            self.record_file = os.path.join(Profiling.logDir,
                                            self.Shape.record_filename)

    def calculate(self):
        """
        Function called at each time step by proteus.
        """
        # store previous values
        self.last_position[:] = self.position
        self.last_velocity[:] = self.velocity
        self.last_acceleration[:] = self.acceleration
        self.last_rotation[:] = self.rotation
        self.last_angvel[:] = self.angvel
        self.last_F[:] = self.F
        self.last_M[:] = self.M
        # for first time step
        try:
            dt = self.model.levelModelList[-1].dt_last
        except:
            dt = self.dt_init
        # update forces and moments for current body/shape
        i0, i1 = self.i_start, self.i_end
        # get forces
        F_p = self.model.levelModelList[-1].coefficients.netForces_p[i0:i1, :]
        F_v = self.model.levelModelList[-1].coefficients.netForces_v[i0:i1, :]
        F_g = self.Fg
        F = np.sum(F_p + F_v, axis=0) + F_g
        # get moments
        M_t = self.model.levelModelList[-1].coefficients.netMoments[i0:i1, :]
        M = np.sum(M_t, axis=0)
        # store F and M with DOF constraints to body
        self.F[:] = F2 = F*self.Shape.free_x
        self.M[:] = M2 = M*self.Shape.free_r
        # calculate new properties
        self.step(dt)
        # log values
        t_previous = self.model.stepController.t_model_last-dt
        t_current = self.model.stepController.t_model_last
        h = self.h
        last_pos, pos = self.last_position, self.position
        last_vel, vel = self.last_velocity, self.velocity
        rot = self.rotation
        rot_x = atan2(rot[1, 2], rot[2, 2])
        rot_y = -asin(rot[0, 2])
        rot_z = atan2(rot[0, 1], rot[0, 0])
        logEvent("================================================================")
        logEvent("=================== Rigid Body Calculation =====================")
        logEvent("================================================================")
        logEvent("Name: " + `self.Shape.name`)
        logEvent("================================================================")
        logEvent("[proteus]     t=%1.5fsec to t=%1.5fsec" % \
            (t_previous, t_current))
        logEvent("[proteus]    dt=%1.5fsec" % (dt))
        logEvent("[body] ============== Pre-calculation attributes  ==============")
        logEvent("[proteus]     t=%1.5fsec" % (t_previous))
        logEvent("[proteus]     F=(% 12.7e, % 12.7e, % 12.7e)" % (F[0], F[1], F[2]))
        logEvent("[proteus] F*DOF=(% 12.7e, % 12.7e, % 12.7e)" % (F2[0], F2[1], F2[2]))
        logEvent("[proteus]     M=(% 12.7e, % 12.7e, % 12.7e)" % (M[0], M[1], M[2]))
        logEvent("[proteus] M*DOF=(% 12.7e, % 12.7e, % 12.7e)" % (M2[0], M2[1], M2[2]))
        logEvent("[body]      pos=(% 12.7e, % 12.7e, % 12.7e)" % \
            (last_pos[0], last_pos[1], last_pos[2]))
        logEvent("[body]      vel=(% 12.7e, % 12.7e, % 12.7e)" % \
            (last_vel[0], last_vel[1], last_vel[2]))
        logEvent("[body] ===============Post-calculation attributes ==============")
        logEvent("[body]        t=%1.5fsec" % (t_current))
        logEvent("[body]        h=(% 12.7e, % 12.7e, % 12.7e)" % (h[0], h[1], h[2]))
        logEvent("[body]      pos=(% 12.7e, % 12.7e, % 12.7e)" % \
            (pos[0], pos[1], pos[2]))
        logEvent("[body]      vel=(% 12.7e, % 12.7e, % 12.7e)" % \
            (vel[0], vel[1], vel[2]))
        logEvent("[body]      rot=(% 12.7e, % 12.7e, % 12.7e)" % \
            (rot_x, rot_y, rot_z))
        logEvent("================================================================")

    def step(self, dt):
        """
        Step for rigid body calculations in Python

        Parameters
        ----------
        dt: float
            time step
        """
        nd = self.Shape.Domain.nd
        # acceleration from force
        self.acceleration = self.F/self.Shape.mass
        # angular acceleration from moment
        if sum(self.M) != 0:
            self.inertia = self.Shape.getInertia(self.M, self.Shape.barycenter)
            assert self.inertia != 0, 'Zero inertia: inertia tensor (It)' \
                                      'was not set correctly!'
            ang_acc = self.M[:]/self.inertia
        else:
            self.inertia = None
            ang_acc = np.array([0., 0., 0.])
        # substeps for smoother motion between timesteps
        ang_disp = 0
        substeps = 20
        dt_sub = dt/float(substeps)
        self.h[:] = np.zeros(3)
        for i in range(substeps):
            # displacement
            self.velocity += self.acceleration*dt_sub
            self.h += self.velocity*dt_sub
            # rotation
            self.angvel += ang_acc*dt_sub
            ang_disp += self.angvel*dt_sub
        # translate
        self.Shape.translate(self.h[:nd])
        # rotate
        self.ang = np.linalg.norm(ang_disp)
        if nd == 2 and self.angvel[2] < 0:
            self.ang = -self.ang
        if self.ang != 0.:
            self.Shape.rotate(self.ang, self.angvel, self.Shape.barycenter)
            self.rotation[:nd, :nd] = self.Shape.coords_system
            self.rotation_matrix[:] = np.dot(np.linalg.inv(self.last_rotation),
                                             self.rotation)
        else:
            self.rotation_matrix[:] = np.eye(3)
        self.barycenter[:] = self.Shape.barycenter
        self.position[:] = self.Shape.barycenter
        if self.Shape.record_values is True:
            self.recordValues()

    def recordValues(self):
        """
        Records values of rigid body attributes at each time step in a csv file.
        """
        comm = Comm.get()
        if comm.isMaster():
            t_last = self.model.stepController.t_model_last
            dt_last = self.model.levelModelList[-1].dt_last
            values_towrite = []
            t = t_last-dt_last
            if t == 0:
                headers = []
                if self.Shape.record_dict['time'] is True:
                    headers += ['t']
                if self.Shape.record_dict['pos'] is True:
                    headers += ['x', 'y', 'z']
                if self.Shape.record_dict['rot'] is True:
                    headers += ['rx', 'ry', 'rz']
                if self.Shape.record_dict['F'] is True:
                    headers += ['Fx', 'Fy', 'Fz']
                if self.Shape.record_dict['M'] is True:
                    headers += ['Mx', 'My', 'Mz']
                if self.Shape.record_dict['inertia'] is True:
                    headers += ['inertia']
                if self.Shape.record_dict['vel'] is True:
                    headers += ['vel_x', 'vel_y', 'vel_z']
                if self.Shape.record_dict['acc'] is True:
                    headers += ['acc_x', 'acc_y', 'acc_z']
                with open(self.record_file, 'w') as csvfile:
                    writer = csv.writer(csvfile, delimiter=',')
                    writer.writerow(headers)
            if self.Shape.record_dict['time'] is True:
                t = t_last-dt_last
                values_towrite += [t]
            if self.Shape.record_dict['pos'] is True:
                x, y, z = self.last_position
                values_towrite += [x, y, z]
            if self.Shape.record_dict['rot'] is True:
                rot = self.last_rotation
                rx = atan2(rot[1, 2], rot[2, 2])
                ry = -asin(rot[0, 2])
                rz = atan2(rot[0, 1], rot[0, 0])
                values_towrite += [rx, ry, rz]
            if self.Shape.record_dict['F'] is True:
                Fx, Fy, Fz = self.F
                values_towrite += [Fx, Fy, Fz]
            if self.Shape.record_dict['M'] is True:
                Mx, My, Mz = self.M
                values_towrite += [Mx, My, Mz]
            if self.Shape.record_dict['inertia'] is True:
                values_towrite += [self.inertia]
            if self.Shape.record_dict['vel'] is True:
                vel_x, vel_y, vel_z = self.velocity
                values_towrite += [vel_x, vel_y, vel_z]
            if self.Shape.record_dict['acc'] is True:
                acc_x, acc_y, acc_z = self.acceleration
                values_towrite += [acc_x, acc_y, acc_z]
            with open(self.record_file, 'a') as csvfile:
                writer = csv.writer(csvfile, delimiter=',')
                writer.writerow(values_towrite)


def assembleDomain(domain):
    """
    This function sets up everything needed for the domain, meshing, and
    AuxiliaryVariables calculations (if any).
    It should always be called after defining and manipulating all the shapes
    to be attached to the domain.

    Parameters
    ----------
    domain: proteus.Domain.D_base
        Domain class instance that hold all the geometrical informations and
        boundary conditions of the shape.
    """
    _assembleGeometry(domain, BC_class=bc.BC_RANS)
    domain.bc[0].setNonMaterial()  # set BC for boundary between processors
    assembleAuxiliaryVariables(domain)
    _generateMesh(domain)


def assembleAuxiliaryVariables(domain):
    """
    Adds the auxiliary variables to the domain.

    Parameters
    ----------
    domain: proteus.Domain.D_base
        Domain class instance that hold all the geometrical informations and
        boundary conditions of the shape.

    Notes
    -----
    Should be called after assembleGeometry
    """

    domain.auxiliaryVariables = {
        'dissipation': [],
        'kappa': [],
        'ls': [],
        'ls_consrv': [],
        'moveMesh': [],
        'redist': [],
        'twp': [],
        'vof': []
    }

    zones_global = {}
    start_region = 0
    start_rflag = 0
    start_flag = 0
    for shape in domain.shape_list:
        aux = domain.auxiliaryVariables
        # ----------------------------
        # RIGID BODIES
        if 'RigidBody' in shape.auxiliaryVariables.keys():
            body = RigidBody(shape)
            aux['twp'] += [body]
            # fixing mesh on rigid body
            for boundcond in shape.BC_list:
                boundcond.setMoveMesh(body.last_position, body.h,
                                      body.rotation_matrix)
            # update the indice for force/moment calculations
            body.i_start = start_flag+1
            body.i_end = start_flag+1+len(shape.BC_list)
        # ----------------------------
        # ABSORPTION/GENERATION ZONES
        if 'ChRigidBody' in shape.auxiliaryVariables.keys():
            body = shape.auxiliaryVariables['ChRigidBody']
            for boundcond in shape.BC_list:
                boundcond.setChMoveMesh(body)
            body.i_start = start_flag+1
            body.i_end = start_flag+1+len(shape.BC_list)

        if 'RelaxZones' in shape.auxiliaryVariables.keys():
            if not zones_global:
                aux['twp'] += [bc.RelaxationZoneWaveGenerator(zones_global,
                                                       domain.nd)]
            if not hasattr(domain, 'porosityTypes'):
                # create arrays of default values
                domain.porosityTypes = np.ones(len(domain.regionFlags)+1)
                domain.dragAlphaTypes = np.zeros(len(domain.regionFlags)+1)
                domain.dragBetaTypes = np.zeros(len(domain.regionFlags)+1)
                domain.epsFact_solid = np.zeros(len(domain.regionFlags)+1)
            i0 = start_region+1
            for flag, zone in shape.zones.iteritems():
                ind = [i for i, f in enumerate(shape.regionFlags) if f == flag]
                for i1 in ind:
                    domain.porosityTypes[i0+i1] = zone.porosity
                    domain.dragAlphaTypes[i0+i1] = zone.dragAlpha
                    domain.dragBetaTypes[i0+i1] = zone.dragBeta
                    domain.epsFact_solid[i0+i1] = zone.epsFact_solid
                # update dict with global key instead of local key
                key = flag+start_rflag
                zones_global[key] = zone
        start_flag += len(shape.BC_list)
        # ----------------------------
        # GAUGES
        gauge_dict = {key: shape.auxiliaryVariables.get(key,[])
                      for key in shape.auxiliaryVariables.keys()
                      if str(key).startswith('Gauge_')}
        for key in gauge_dict.keys():
            key_name = key.split('_', 1)[1] # Cutting off "Gauge_" prefix
            if key_name not in aux:
                # It is probably too dangerous to simply put "aux[key_name] = []"
                # as this system is fragile to typos. Instead, we throw an error.
                raise ValueError('ERROR: Gauge key ',
                                 key_name,
                                 ' is not a recognized model by SpatialTools.',
                                 ' The known models in our dictionary are ',
                                 str(aux.keys())
                                 )
            else:
                aux[key_name] += gauge_dict[key]
        if shape.regions is not None:
            start_region += len(shape.regions)
            start_rflag += max(domain.regionFlags[0:start_region])


def get_unit_vector(vector):
    return np.array(vector)/np.linalg.norm(vector)
