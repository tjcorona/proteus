from distutils.core import setup, Extension
import numpy

## \file setup.py setup.py
#  \brief The python script for building proteus
#
#  Set the DISTUTILS_DEBUG environment variable to print detailed information while setup.py is running.
#
try:
    from config import *
except:
    raise RuntimeError("Missing or invalid config.py file. See proteusConfig for examples")

###to turn on debugging in c++
##\todo Finishing cleaning up setup.py/setup.cfg, config.py...
from distutils import sysconfig
cv = sysconfig.get_config_vars()
cv["OPT"] = cv["OPT"].replace("-DNDEBUG","-DDEBUG")
cv["OPT"] = cv["OPT"].replace("-O3","-g")
cv["CFLAGS"] = cv["CFLAGS"].replace("-DNDEBUG","-DDEBUG")
cv["CFLAGS"] = cv["CFLAGS"].replace("-O3","-g")

setup(name='proteus',
      version='1.0.0',
      description='Python tools for multiphysics modeling',
      author='Chris Kees, Matthew Farthing, et al.',
      author_email='chris.kees@us.army.mil',
      url='http://proteus.usace.army.mil',
      packages = ['proteus'],
      package_dir={'proteus':'src'},
      ext_package='proteus',
      ext_modules=[Extension('ctracking',
                             ['src/ctrackingModule.cpp','src/tracking.cpp'],
                             define_macros=[('PROTEUS_SUPERLU_H',PROTEUS_SUPERLU_H)],
                             include_dirs=[numpy.get_include(),'include',
                                           PROTEUS_SUPERLU_INCLUDE_DIR],
                             libraries=['m'],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('cellam',
                             ['src/cellamModule.cpp','src/ellam.cpp'],
                             define_macros=[('PROTEUS_SUPERLU_H',PROTEUS_SUPERLU_H),
                                            ('PROTEUS_LAPACK_H',PROTEUS_LAPACK_H),
                                            ('PROTEUS_LAPACK_INTEGER',PROTEUS_LAPACK_INTEGER),
                                            ('PROTEUS_BLAS_H',PROTEUS_BLAS_H)],
                             include_dirs=[numpy.get_include(),'include',
                                           PROTEUS_SUPERLU_INCLUDE_DIR],
                             libraries=['m'],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('cfemIntegrals',
                             ['src/cfemIntegralsModule.c','src/femIntegrals.c','src/postprocessing.c'],
                             define_macros=[('PROTEUS_SUPERLU_H',PROTEUS_SUPERLU_H),
                                            ('PROTEUS_LAPACK_H',PROTEUS_LAPACK_H),
                                            ('PROTEUS_LAPACK_INTEGER',PROTEUS_LAPACK_INTEGER),
                                            ('PROTEUS_BLAS_H',PROTEUS_BLAS_H)],
                             include_dirs=[numpy.get_include(),'include',
                                           PROTEUS_SUPERLU_INCLUDE_DIR,
                                           PROTEUS_LAPACK_INCLUDE_DIR,
                                           PROTEUS_BLAS_INCLUDE_DIR],
                             library_dirs=[PROTEUS_LAPACK_LIB_DIR,
                                           PROTEUS_BLAS_LIB_DIR],
                             libraries=['m',PROTEUS_LAPACK_LIB,PROTEUS_BLAS_LIB],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('cmeshTools',
                             ['src/cmeshToolsModule.cpp','src/mesh.cpp','src/meshio.cpp'],
                             define_macros=[('PROTEUS_SUPERLU_H',PROTEUS_SUPERLU_H),
                                            ('PROTEUS_TRIANGLE_H',PROTEUS_TRIANGLE_H)],
                             include_dirs=([numpy.get_include(),'include']+
                                           [PROTEUS_TRIANGLE_INCLUDE_DIR]),
                             libraries=['m',PROTEUS_DAETK_LIB]+[PROTEUS_TRIANGLE_LIB],
                             library_dirs=[PROTEUS_DAETK_LIB_DIR]+[PROTEUS_TRIANGLE_LIB_DIR],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('ctransportCoefficients',
                             ['src/ctransportCoefficientsModule.c','src/transportCoefficients.c'],
                             include_dirs=[numpy.get_include(),'include'],
                             libraries=['m'],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('csubgridError',
                             ['src/csubgridErrorModule.c','src/subgridError.c'],
                             include_dirs=[numpy.get_include(),'include'],
                             libraries=['m'],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('cshockCapturing',
                             ['src/cshockCapturingModule.c','src/shockCapturing.c'],
                             include_dirs=[numpy.get_include(),'include'],
                             libraries=['m'],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('lapackWrappers',
                             ['src/lapackWrappersModule.c'],
                             define_macros=[('PROTEUS_LAPACK_H',PROTEUS_LAPACK_H),
                                            ('PROTEUS_LAPACK_INTEGER',PROTEUS_LAPACK_INTEGER),
                                            ('PROTEUS_BLAS_H',PROTEUS_BLAS_H)],
                             include_dirs=[numpy.get_include(),'include',
                                           PROTEUS_LAPACK_INCLUDE_DIR,
                                           PROTEUS_BLAS_INCLUDE_DIR],
                             library_dirs=[PROTEUS_LAPACK_LIB_DIR,PROTEUS_BLAS_LIB_DIR],
                             libraries=['m',
                                        PROTEUS_LAPACK_LIB,
                                        PROTEUS_BLAS_LIB],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('superluWrappers',
                             ['src/superluWrappersModule.c'],
                             define_macros=[('PROTEUS_SUPERLU_H',PROTEUS_SUPERLU_H),
                                            ('PROTEUS_BLAS_H',PROTEUS_BLAS_H)],
                             include_dirs=[numpy.get_include(),'include',PROTEUS_SUPERLU_INCLUDE_DIR],
                             library_dirs=[PROTEUS_SUPERLU_LIB_DIR,PROTEUS_LAPACK_LIB_DIR,PROTEUS_BLAS_LIB_DIR],
                             libraries=['m',PROTEUS_SUPERLU_LIB,PROTEUS_LAPACK_LIB,PROTEUS_BLAS_LIB],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('triangleWrappers',
                             ['src/triangleWrappersModule.c'],
                             define_macros=[('PROTEUS_TRIANGLE_H',
                                             PROTEUS_TRIANGLE_H),
                                            ('MWF_ADDED_FLAGS',
                                             1)],
                             include_dirs=[numpy.get_include(),PROTEUS_TRIANGLE_INCLUDE_DIR],
                             library_dirs=[PROTEUS_TRIANGLE_LIB_DIR],
                             libraries=['m',
                                        PROTEUS_TRIANGLE_LIB],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('testStuffImpl',
                             ['src/testStuffImplModule.c','src/testStuffImpl.c'],
                             define_macros=[('MWF_ADDED_FLAGS',
                                             1),
                                            ('PROTEUS_LAPACK_H',PROTEUS_LAPACK_H),
                                            ('PROTEUS_LAPACK_INTEGER',PROTEUS_LAPACK_INTEGER)
                                            ],
                             include_dirs=[numpy.get_include(),'include',
                                           PROTEUS_LAPACK_INCLUDE_DIR
                                           ],
                             library_dirs=[PROTEUS_LAPACK_LIB_DIR],
                             libraries=['m',PROTEUS_LAPACK_LIB],
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS,
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS),
                   Extension('csmoothers',
                             ['src/csmoothersModule.c', 'src/smoothers.c'],
                             define_macros=[('PROTEUS_SUPERLU_H',PROTEUS_SUPERLU_H),
                                            ('PROTEUS_LAPACK_H',PROTEUS_LAPACK_H),
                                            ('PROTEUS_LAPACK_INTEGER',PROTEUS_LAPACK_INTEGER),
                                            ('PROTEUS_BLAS_H',PROTEUS_BLAS_H)],
                             include_dirs=[numpy.get_include(),'include',
                                           PROTEUS_SUPERLU_INCLUDE_DIR,
                                           PROTEUS_LAPACK_INCLUDE_DIR,
                                           PROTEUS_BLAS_INCLUDE_DIR
                                           ],
                             library_dirs=[PROTEUS_SUPERLU_INCLUDE_DIR,
                                           PROTEUS_SUPERLU_LIB_DIR,
                                           PROTEUS_LAPACK_LIB_DIR,
                                           PROTEUS_BLAS_LIB_DIR],
                             libraries=['m',
                                        PROTEUS_SUPERLU_LIB,
                                        PROTEUS_LAPACK_LIB,
                                        PROTEUS_BLAS_LIB],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('ctimeIntegration',
                             ['src/ctimeIntegrationModule.c','src/timeIntegration.c'],
                             include_dirs=[numpy.get_include(),'include'],
                             libraries=['m'],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('canalyticalSolutions',
                             ['src/canalyticalSolutionsModule.c','src/analyticalSolutions.c'],
                             include_dirs=[numpy.get_include(),'include'],
                             libraries=['m'],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('cpostprocessing',
                             ['src/cpostprocessingModule.c','src/postprocessing.c','src/femIntegrals.c'],
                             define_macros=[('MWF_ADDED_FLAGS',
                                             1),
                                            ('PROTEUS_LAPACK_H',PROTEUS_LAPACK_H),
                                            ('PROTEUS_LAPACK_INTEGER',PROTEUS_LAPACK_INTEGER)
                                            ],
                             include_dirs=[numpy.get_include(),'include',
                                           PROTEUS_LAPACK_INCLUDE_DIR
                                           ],
                             library_dirs=[PROTEUS_LAPACK_LIB_DIR,PROTEUS_BLAS_LIB_DIR],
                             libraries=['m',PROTEUS_LAPACK_LIB,PROTEUS_BLAS_LIB],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('cnumericalFlux',
                             ['src/cnumericalFluxModule.c','src/numericalFlux.c'],
                             include_dirs=[numpy.get_include(),'include'],
                             libraries=['m'],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('cTwophaseDarcyCoefficients',
                             ['src/cTwophaseDarcyCoefficientsModule.cpp','src/SubsurfaceTransportCoefficients.cpp'],
                             include_dirs=[numpy.get_include(),'include'],
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS,
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS),
                   Extension('cpskRelations',
                             ['src/cpskRelationsModule.cpp','src/SubsurfaceTransportCoefficients.cpp'],
                             include_dirs=['include'],
                             libraries=['m'],
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS,
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS),
                   Extension('cSubsurfaceTransportCoefficients',
                             ['src/cSubsurfaceTransportCoefficientsModule.cpp','src/SubsurfaceTransportCoefficients.cpp'],
                             include_dirs=[numpy.get_include(),'include'],
                             libraries=['m'],
                             extra_link_args=PROTEUS_EXTRA_LINK_ARGS,
                             extra_compile_args=PROTEUS_EXTRA_COMPILE_ARGS),
                   #Cython generated modules with just c code
                   Extension("waveFunctions",['src/waveFunctions.c','src/transportCoefficients.c'],
                             include_dirs=[numpy.get_include(),'include'])
                   ],
      data_files=[('proteusConfig',['config.py'])],
      scripts = ['scripts/parun','scripts/gf2poly','scripts/gatherArchives.py','scripts/qtm','scripts/waves2xmf','scripts/velocity2xmf'],
      requires=['numpy']
      )
