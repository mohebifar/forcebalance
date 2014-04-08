import os
import numpy as np

from forcebalance.finite_difference import fdwrap, f12d3p
from forcebalance.molecule import Molecule
from forcebalance.nifty import col, flat, statisticalInefficiency
from forcebalance.nifty import printcool

from collections import OrderedDict

from forcebalance.output import getLogger
logger = getLogger(__name__)

# method mean_stderr
def mean_stderr(ts):
    """Return mean and standard deviation of a time series ts."""
    return np.mean(ts), \
      np.std(ts)*np.sqrt(statisticalInefficiency(ts, warn=False)/len(ts))

# method energy_derivatives
def energy_derivatives(engine, FF, mvals, h, pgrad, length, AGrad=True):
    """Compute the first derivatives of a set of snapshot energies with respect
    to the force field parameters. The function calls the finite
    difference subroutine on the energy_driver subroutine also in this
    script.

    Parameters
    ----------
    engine : Engine
        Use this Engine (`GMX`,`TINKER`,`OPENMM` etc.) object to get the energy
        snapshots.
    FF : FF
       Force field object.
    mvals : list
        Mathematical parameter values.
    h : float
        Finite difference step size.
    length : int
        Number of snapshots (length of energy trajectory).
    AGrad : Boolean
        Switch that turns derivatives on or off; if off, return all zeros.

    Returns
    -------
    G : np.array
        Derivative of the energy in a FF.np x length array.
    
    """
    G = np.zeros((FF.np, length))
    
    if not AGrad:
        return G
    def energy_driver(mvals_):
        FF.make(mvals_)
        return engine.energy()

    ED0 = energy_driver(mvals)
        
    for i in pgrad:
        logger.info("%i %s\r" % (i, (FF.plist[i] + " "*30)))
        EDG, _   = f12d3p(fdwrap(energy_driver, mvals, i), h, f0=ED0)

        G[i,:]   = EDG[:]
    return G

class Observable(object):
    """
    Base class for thermodynamical observable used for fitting. This can
    be any experimental data that can be calculated as an ensemble
    average from a simulation.

    Data attributes
    ---------------
    name : string
        Identifier for the observable that is specified in `observables` in Target
        options.
    sreq : list of (strings or lists)
        The names of simulation types that are needed to calculate
        this observable.  If a string is added to the list, then that
        simulation is *required* to calculate the observable.  If a
        list is added, then any simulation within that type is allowed,
        but the first member of the list is used by default.
    treq : list of strings
        The names of timeseries from each simulation that are needed to
        calculate this observable.
    dreq : list of strings
        The names of timeseries from each simulation - in addition to
        "treq" - that are needed to *differentiate* this observable.
        (Usually energy derivatives)
    """
    def __init__(self, source, name=None):
        self.name        = name if name is not None else "empty"
        self.sreq = []
        self.treq = []
        self.dreq = ['energy_derivatives']
                    
    def __str__(self):
        return "observable is " + self.name.capitalize() + "."

    def extract(self, engines, FF, mvals, h, AGrad=True):
        """Calculate and extract the observable from MD results. How this is done
        depends on the observable and the engine so this must be
        implemented in the subclass.

        Parameters
        ----------
        engines : list
            A list of Engine objects that are requred to calculate the observable.
        FF : FF
            Force field object.
        mvals : list
            Mathematical parameter values.
        h : float
            Finite difference step size.
        AGrad : Boolean
            Switch that turns derivatives on or off; if off, return all zeros.
        
        Returns
        -------
        result : (float, float, np.array)
            The returned tuple is (Q, Qerr, Qgrad), where Q is the calculated
            observable, Qerr is the calculated standard deviation of the observable,
            and Qgrad is a M-array with the calculated gradients for the
            observable, with M being the number of force field parameters that are
            being fitted. 
        
        """
        logger.error("Extract method not implemented in base class.\n")    
        raise NotImplementedError

# class Observable_Density
class Observable_Density(Observable):
    def __init__(self, source, name=None):
        """ Density. """
        super(Observable_Density, self).__init__(source, name)
        
        self.name = name if name is not None else "density"

        # Calculating the density requires either a liquid or solid simulation.
        self.sreq = [['liquid', 'solid']]

        # Requires timeseries of densities from the simulation.
        self.treq = ['density']

    def extract(self, engines, FF, mvals, h, pgrad, AGrad=True):         
        #==========================================#
        #  Physical constants and local variables. #
        #==========================================#
        # Energies in kJ/mol and lengths in nanometers.
        kB    = 0.008314472471220214
        kT    = kB*self.temperature
        Beta  = 1.0/kT
        mBeta = -Beta
 
        #======================================================#
        #  Get simulation properties depending on the engines. #
        #======================================================#
        if self.engname == "gromacs":
            # Default name
            deffnm = os.path.basename(os.path.splitext(engines[0].mdene)[0])
            # What energy terms are there and what is their order
            energyterms = engines[0].energy_termnames(edrfile="%s.%s" % (deffnm, "edr"))
            # Grab energy terms to print and keep track of energy term order.
            ekeep  = ['Total-Energy', 'Potential', 'Kinetic-En.', 'Temperature']
            ekeep += ['Volume', 'Density']

            ekeep_order = [key for (key, value) in
                           sorted(energyterms.items(), key=lambda (k, v) : v)
                           if key in ekeep]

            # Perform energy component analysis and return properties.
            engines[0].callgmx(("g_energy " +
                                "-f %s.%s " % (deffnm, "edr") +
                                "-o %s-energy.xvg " % deffnm +
                                "-xvg no"),
                                stdin="\n".join(ekeep))
            
        # Read data and store properties by grabbing columns in right order.
        data        = np.loadtxt("%s-energy.xvg" % deffnm)            
        Energy      = data[:, ekeep_order.index("Total-Energy") + 1]
        Potential   = data[:, ekeep_order.index("Potential") + 1]
        Kinetic     = data[:, ekeep_order.index("Kinetic-En.") + 1]
        Volume      = data[:, ekeep_order.index("Volume") + 1]
        Temperature = data[:, ekeep_order.index("Temperature") + 1]
        Density     = data[:, ekeep_order.index("Density") + 1]

        #============================================#
        #  Compute the potential energy derivatives. #
        #============================================#
        logger.info(("Calculating potential energy derivatives " +
                     "with finite difference step size: %f\n" % h))
        printcool("Initializing array to length %i" % len(Energy),
                  color=4, bold=True)    
        G = energy_derivatives(engines[0], FF, mvals, h, pgrad, len(Energy), AGrad)
        
        #=========================================#
        #  Observable properties and derivatives. #
        #=========================================#
        # Average and error.
        Rho_avg, Rho_err = mean_stderr(Density)
        # Analytic first derivative.
        Rho_grad = mBeta * (flat(np.mat(G) * col(Density)) / len(Density) \
                            - np.mean(Density) * np.mean(G, axis=1))
            
        return Rho_avg, Rho_err, Rho_grad

# class Observable_H_vap
class Observable_H_vap(Observable):
    def __init__(self, source, name=None):
        """ Enthalpy of vaporization. """
        super(Observable_H_vap, self).__init__(source, name)
        
        self.name = name if name is not None else "H_vap"

        # Calculating the heat of vaporization requires a liquid simulation and a gas simulation.
        self.sreq = [['liquid'], ['gas']]

        # Requires timeseries of energies and volumes from the simulation.
        self.treq = [['energy', 'volume'], ['energy']]

    def extract(self, engines, FF, mvals, h, pgrad, AGrad=True): 
        #==========================================#
        #  Physical constants and local variables. #
        #==========================================#
        # Energies in kJ/mol and lengths in nanometers.
        kB      = 0.008314472471220214
        kT      = kB*self.temperature
        Beta    = 1.0/kT
        mBeta   = -Beta
        # Conversion factor between 1 kJ/mol -> bar nm^3 
        pconv   = 16.6054

        # Number of molecules in the liquid phase.
        mol     = Molecule(os.path.basename(os.path.splitext(engines[0].mdtraj)[0]) +
                           ".gro")
        nmol = len(mol.molecules)

        #======================================================#
        #  Get simulation properties depending on the engines. #
        #======================================================#
        if self.engname == "gromacs":
            # Default names
            deffnm1 = os.path.basename(os.path.splitext(engines[0].mdene)[0])
            deffnm2 = os.path.basename(os.path.splitext(engines[1].mdene)[0])
            # Figure out which energy terms and present and their order. 
            energyterms1 = engines[0].energy_termnames(edrfile="%s.%s" % (deffnm1, "edr"))
            energyterms2 = engines[1].energy_termnames(edrfile="%s.%s" % (deffnm2, "edr"))
            # Grab energy terms to print and keep track of energy term order.
            ekeep1  = ['Total-Energy', 'Potential', 'Kinetic-En.', 'Temperature', 'Volume']
            ekeep2  = ['Total-Energy', 'Potential', 'Kinetic-En.', 'Temperature']

            ekeep_order1 = [key for (key, value)
                            in sorted(energyterms1.items(), key=lambda (k, v) : v)
                            if key in ekeep1]
            ekeep_order2 = [key for (key, value)
                            in sorted(energyterms2.items(), key=lambda (k, v) : v)
                            if key in ekeep2]

            # Perform energy component analysis and return properties.
            engines[0].callgmx(("g_energy " +
                                "-f %s.%s " % (deffnm1, "edr") +
                                "-o %s-energy.xvg " % deffnm1 +
                                "-xvg no"),
                                stdin="\n".join(ekeep1))
            engines[1].callgmx(("g_energy " +
                                "-f %s.%s " % (deffnm2, "edr") +
                                "-o %s-energy.xvg " % deffnm2 +
                                "-xvg no"),
                                stdin="\n".join(ekeep2))

        # Read data and store properties by grabbing columns in right order.
        data1       = np.loadtxt("%s-energy.xvg" % deffnm1)
        data2       = np.loadtxt("%s-energy.xvg" % deffnm2)
        Energy      = data1[:, ekeep_order1.index("Total-Energy") + 1]
        Potential   = data1[:, ekeep_order1.index("Potential") + 1]
        Kinetic     = data1[:, ekeep_order1.index("Kinetic-En.") + 1]
        Temperature = data1[:, ekeep_order1.index("Temperature") + 1]
        Volume      = data1[:, ekeep_order1.index("Volume") + 1]
        mEnergy     = data2[:, ekeep_order2.index("Total-Energy") + 1]
        mPotential  = data2[:, ekeep_order2.index("Potential") + 1]
        mKinetic    = data2[:, ekeep_order2.index("Kinetic-En.") + 1]
        
        #============================================#
        #  Compute the potential energy derivatives. #
        #============================================#
        logger.info(("Calculating potential energy derivatives " +
                     "with finite difference step size: %f\n" % h))
        printcool("Initializing arrays to lengths %d" % len(Energy),
                  color=4, bold=True)
        
        G  = energy_derivatives(engines[0], FF, mvals, h, pgrad, len(Energy), AGrad)
        Gm = energy_derivatives(engines[1], FF, mvals, h, pgrad, len(mEnergy), AGrad)
                
        #=========================================#
        #  Observable properties and derivatives. #
        #=========================================#
        # Average and error.
        E_avg, E_err     = mean_stderr(Energy)
        Em_avg, Em_err   = mean_stderr(mEnergy)
        Vol_avg, Vol_err = mean_stderr(Volume)
                
        Hvap_avg = Em_avg - E_avg/nmol - self.pressure*Vol_avg/nmol/pconv + kT 
        Hvap_err = np.sqrt((E_err/nmol)**2 + Em_err**2
                           + (self.pressure**2) * (Vol_err**2)/(float(nmol)**2)/(pconv**2))
        # Analytic first derivative.
        Hvap_grad  = np.mean(Gm, axis=1)
        Hvap_grad += mBeta * (flat(np.mat(Gm) * col(mEnergy)) / len(mEnergy) \
                               - np.mean(mEnergy) * np.mean(Gm, axis=1))
        Hvap_grad -= np.mean(G, axis=1)/nmol
        Hvap_grad += Beta * (flat(np.mat(G) * col(Energy)) / len(Energy) \
                               - np.mean(Energy) * np.mean(G, axis=1))/nmol
        Hvap_grad += (Beta*self.pressure/nmol/pconv) * \
          (flat(np.mat(G) * col(Volume)) / len(Volume) \
           - np.mean(Volume) * np.mean(G, axis=1))

        return Hvap_avg, Hvap_err, Hvap_grad

# class Observable_Kappa
class Observable_Kappa(Observable):
    def __init__(self, source, name=None):
        """ Compressibility (applies to liquid and lipid bilayer.) """
        super(Observable_H_vap, self).__init__(source, name)
        
        self.name = name if name is not None else "H_vap"

        # List of dictionaries of simulations, keyed to timeseries to extract from the simulation.
        # Each dictionary represents a simulation in a sequence, but the observable isn't mapped to a unique simulation each time.
        # Because of this, we determine which simulation to launch (in the sequence) based on the available initial coordinates (or explicit user input).
        # Depending on which simulation is executed, we require different timeseries from the simulation, and different formulas.
        # But another way is to just define two observables ... need to think about it.

        self.sreq = [{'liquid':['volume'], 'bilayer':['al']},
                     ]
        

        # Requires timeseries of energies and volumes from the simulation.
        self.treq = [['energy', 'volume'], ['energy']]
