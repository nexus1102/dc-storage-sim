"""Independent worst-probability LP, also used to verify robust model dualization."""
import numpy as np
from scipy.optimize import linprog

def worst_probabilities(costs,q=None,radius=.2):
    costs=np.asarray(costs,float); n=len(costs)
    q=np.ones(n)/n if q is None else np.asarray(q,float)
    if n==0 or not np.isfinite(costs).all() or len(q)!=n or (q<0).any() or abs(q.sum()-1)>1e-10 or not 0<=radius<=2:
        raise ValueError('invalid ambiguity set')
    eye=np.eye(n)
    a=np.vstack([np.c_[eye,-eye],np.c_[-eye,-eye],np.r_[np.zeros(n),np.ones(n)][None,:]])
    b=np.r_[q,-q,radius]
    result=linprog(np.r_[-costs,np.zeros(n)],A_ub=a,b_ub=b,A_eq=np.r_[np.ones(n),np.zeros(n)][None,:],b_eq=[1.],bounds=(0,None),method='highs')
    if not result.success: raise RuntimeError(result.message)
    p=result.x[:n]
    return dict(probabilities=p,cost=float(p@costs),l1=float(np.abs(p-q).sum()),radius=radius)
