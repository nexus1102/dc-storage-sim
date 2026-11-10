"""L1 transportation decomposition of aggregate deferred cohorts into deadlines.

Each release-slot is a divisible job cohort. Trace lacks checkpoint/deadline labels;
this certifies the explicitly assumed divisible-job model, not real pod scheduling.
"""
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

def decompose(defer,recover,window_h,dt=.25):
    d=np.maximum(0,np.asarray(defer))*dt; r=np.maximum(0,np.asarray(recover))*dt
    n=len(d); w=int(np.floor(window_h/dt+1e-9))
    arcs=[(i,j) for i in range(n) for j in range(i,min(n,i+w+1)) if d[i]>1e-10]
    # Unmatched arrival/recovery slacks make infeasible aggregates observable by L1.
    a=lil_matrix((2*n,len(arcs)+4*n))
    for k,(i,j) in enumerate(arcs): a[i,k]=1; a[n+j,k]=1
    for i in range(2*n): a[i,len(arcs)+2*i]=1; a[i,len(arcs)+2*i+1]=-1
    objective=np.r_[np.zeros(len(arcs)),np.ones(4*n)]
    sol=linprog(objective,A_eq=a.tocsr(),b_eq=np.r_[d,r],bounds=(0,None),method='highs')
    if not sol.success: raise RuntimeError(sol.message)
    return dict(l1=float(sol.fun),passed=bool(sol.fun<1e-6),allocations=[(i,j,float(sol.x[k])) for k,(i,j) in enumerate(arcs) if sol.x[k]>1e-9])
