import networkx as nx
import csv
from scipy import sparse as sp
from scipy.sparse import csgraph
import scipy.sparse.linalg as splinalg
import numpy as np
import pandas as pd
import warnings
import collections as cole
from .cpp import *

import gzip
import bz2
import lzma

import multiprocessing as mp

def _load_from_shared(sabuf, dtype, shape):
    return np.frombuffer(sabuf, dtype=dtype).reshape(shape)

""" Create shared memory that can be passed to a child process,
wrapped in a numpy array."""
def _copy_to_shared(a):
    # determine the numpy type of a.
    dtype = a.dtype
    shape = a.shape
    sabuf = mp.RawArray(ctypes.c_uint8, a.nbytes)
    sa = _load_from_shared(sabuf, dtype, shape)
    np.copyto(sa, a) # make a copy
    return sa, (sabuf, dtype, shape)


class GraphLocal:
    """
    This class implements graph loading from an edgelist, gml or graphml and provides methods that operate on the graph.

    Attributes
    ----------
    adjacency_matrix : scipy csr matrix

    ai : numpy vector
        CSC format index pointer array, its data type is determined by "itype" during initialization

    aj : numpy vector
        CSC format index array, its data type is determined by "vtype" during initialization

    _num_vertices : int
        Number of vertices

    _num_edges : int
        Number of edges

    _weighted : boolean
        Declares if it is a weighted graph or not

    d : float64 numpy vector
        Degrees vector

    dn : float64 numpy vector
        Component-wise reciprocal of degrees vector

    d_sqrt : float64 numpy vector
        Component-wise square root of degrees vector

    dn_sqrt : float64 numpy vector
        Component-wise reciprocal of sqaure root degrees vector

    vol_G : float64 numpy vector
        Volume of graph

    components : list of sets
        Each set contains the indices of a connected component of the graph

    number_of_components : int
        Number of connected components of the graph

    bicomponents : list of sets
        Each set contains the indices of a biconnected component of the graph

    number_of_bicomponents : int
        Number of connected components of the graph

    core_numbers : dictionary
        Core number for each vertex

    Methods
    -------

    read_graph(filename, file_type='edgelist', separator='\t')
        Reads the graph from a file

    compute_statistics()
        Computes statistics for the graph

    connected_components()
        Computes the connected components of the graph

    is_disconnected()
        Checks if graph is connected

    biconnected_components():
        Computes the biconnected components of the graph

    core_number()
        Returns the core number for each vertex

    neighbors(vertex)
        Returns a list with the neighbors of the given vertex

    list_to_gl(source,target)
        Create a GraphLocal object from edge list
    """
    def __init__(self,
        filename = None,
        file_type='edgelist',
        separator='\t',
        remove_whitespace=False,header=False, headerrow=None,
        vtype=np.uint32,itype=np.uint32):
        """
        Initializes the graph from a gml or a edgelist file and initializes the attributes of the class.

        Parameters
        ----------
        See read_graph for a description of the parameters.
        """

        if filename != None:
            self.read_graph(filename, file_type = file_type, separator = separator, remove_whitespace = remove_whitespace,
                header = header, headerrow = headerrow, vtype=vtype, itype=itype)

    def read_graph(self, filename, file_type='edgelist', separator='\t', remove_whitespace=False, header=False, headerrow=None, vtype=np.uint32, itype=np.uint32):
        """
        Reads the graph from an edgelist, gml or graphml file and initializes the class attribute adjacency_matrix.

        Parameters
        ----------
        filename : string
            Name of the file, for example 'JohnsHopkins.edgelist', 'JohnsHopkins.gml', 'JohnsHopkins.graphml'.

        file_type : string
            Type of file. Currently only 'edgelist', 'gml' and 'graphml' are supported.
            Default = 'edgelist'

        separator : string
            used if file_type = 'edgelist'
            Default = '\t'

        remove_whitespace : bool
            set it to be True when there is more than one kinds of separators in the file
            Default = False

        header : bool
            This lets the first line of the file contain a set of heade
            information that should be ignore_index
            Default = False

        headerrow : int
            Use which row as column names. This argument takes precidence over
            the header=True using headerrow = 0
            Default = None

        vtype
            numpy integer type of CSC format index array
            Default = np.uint32

        itype
            numpy integer type of CSC format index pointer array
            Default = np.uint32
        """
        if file_type == 'edgelist':

            #dtype = {0:'int32', 1:'int32', 2:'float64'}
            if header and headerrow is None:
                headerrow = 0

            if remove_whitespace:
                df = pd.read_csv(filename, header=headerrow, delim_whitespace=remove_whitespace)
            else:
                df = pd.read_csv(filename, sep=separator, header=headerrow, delim_whitespace=remove_whitespace)
            cols = [0,1,2]
            if header != None:
                cols = list(df.columns)
            source = df[cols[0]].values
            target = df[cols[1]].values
            if df.shape[1] == 2:
                weights = np.ones(source.shape[0])
            elif df.shape[1] == 3:
                weights = df[cols[2]].values
            else:
                raise Exception('GraphLocal.read_graph: df.shape[1] not in (2, 3)')
            self._num_vertices = max(source.max() + 1, target.max()+1)
            #self.adjacency_matrix = source, target, weights

            self.adjacency_matrix = sp.csr_matrix((weights.astype(np.float64), (source, target)), shape=(self._num_vertices, self._num_vertices))

        elif file_type == 'gml':
            warnings.warn("Loading a gml is not efficient, we suggest using an edgelist format for this API.")
            G = nx.read_gml(filename).to_undirected()
            self.adjacency_matrix = nx.adjacency_matrix(G).astype(np.float64)
            self._num_vertices = nx.number_of_nodes(G)

        elif file_type == 'graphml':
            warnings.warn("Loading a graphml is not efficient, we suggest using an edgelist format for this API.")
            G = nx.read_graphml(filename).to_undirected()
            self.adjacency_matrix = nx.adjacency_matrix(G).astype(np.float64)
            self._num_vertices = nx.number_of_nodes(G)

        else:
            print('This file type is not supported')
            return


        self._weighted = False
        for i in self.adjacency_matrix.data:
            if i != 1:
                self._weighted = True
                break
        is_symmetric = (self.adjacency_matrix != self.adjacency_matrix.T).sum() == 0
        if not is_symmetric:
            # Symmetrize matrix, choosing larger weight
            sel = self.adjacency_matrix.T > self.adjacency_matrix
            self.adjacency_matrix = self.adjacency_matrix - self.adjacency_matrix.multiply(sel) + self.adjacency_matrix.T.multiply(sel)
            assert (self.adjacency_matrix != self.adjacency_matrix.T).sum() == 0

        self._num_edges = self.adjacency_matrix.nnz
        self.compute_statistics()
        self.ai = itype(self.adjacency_matrix.indptr)
        self.aj = vtype(self.adjacency_matrix.indices)

    @classmethod
    def from_networkx(cls,G):
        """
        Create a GraphLocal object from a networkx graph.

        Paramters
        ---------
        G
            The networkx graph.
        """
        if G.is_directed() == True:
            raise Exception("from_networkx requires an undirected graph, use G.to_undirected()")
        rval = cls()
        rval.adjacency_matrix = nx.adjacency_matrix(G).astype(np.float64)
        rval._num_vertices = nx.number_of_nodes(G)

        # TODO, use this in the read_graph
        rval._weighted = False
        for i in rval.adjacency_matrix.data:
            if i != 1:
                rval._weighted = True
                break

        # automatically determine sizes
        if G.number_of_nodes() < 4294967295:
            vtype = np.uint32
        else:
            vtype = np.int64
        if 2*G.number_of_edges() < 4294967295:
            itype = np.uint32
        else:
            itype = np.int64

        rval._num_edges = rval.adjacency_matrix.nnz
        rval.compute_statistics()
        rval.ai = itype(rval.adjacency_matrix.indptr)
        rval.aj = vtype(rval.adjacency_matrix.indices)
        return rval

    def list_to_gl(self,source,target,weights,vtype=np.uint32, itype=np.uint32):
        """
        Create a GraphLocal object from edge list.

        Parameters
        ----------
        source
            A numpy array of sources for the edges

        target
            A numpy array of targets for the edges

        weights
            A numpy array of weights for the edges

        vtype
            numpy integer type of CSC format index array
            Default = np.uint32

        itype
            numpy integer type of CSC format index pointer array
            Default = np.uint32
        """

        # TODO, fix this up to avoid duplicating code with read...

        source = np.array(source,dtype=vtype)
        target = np.array(target,dtype=vtype)
        weights = np.array(weights,dtype=np.double)

        self._num_edges = len(source)
        self._num_vertices = max(source.max() + 1, target.max()+1)
        self.adjacency_matrix = sp.csr_matrix((weights.astype(np.float64), (source, target)), shape=(self._num_vertices, self._num_vertices))
        self._weighted = False
        for i in self.adjacency_matrix.data:
            if i != 1:
                self._weighted = True
                break
        is_symmetric = (self.adjacency_matrix != self.adjacency_matrix.T).sum() == 0
        if not is_symmetric:
            # Symmetrize matrix, choosing larger weight
            sel = self.adjacency_matrix.T > self.adjacency_matrix
            self.adjacency_matrix = self.adjacency_matrix - self.adjacency_matrix.multiply(sel) + self.adjacency_matrix.T.multiply(sel)
            assert (self.adjacency_matrix != self.adjacency_matrix.T).sum() == 0

        self._num_edges = self.adjacency_matrix.nnz
        self.compute_statistics()
        self.ai = itype(self.adjacency_matrix.indptr)
        self.aj = vtype(self.adjacency_matrix.indices)

    def discard_weights(self):
        """ Discard any weights that were loaded from the data file.
        This sets all the weights associated with each edge to 1.0,
        which is our "no weight" case."""
        self.adjacency_matrix.data.fill(1.0)
        self._weighted = False
        self.compute_statistics()

    def compute_statistics(self):
        """
        Computes statistics for the graph. It updates the class attributes.
        The user needs to read the graph first before calling
        this method by calling the read_graph method from this class.
        """
        self.d = np.ravel(self.adjacency_matrix.sum(axis=1))
        self.dn = np.zeros(self._num_vertices)
        self.dn[self.d != 0] = 1.0 / self.d[self.d != 0]
        self.d_sqrt = np.sqrt(self.d)
        self.dn_sqrt = np.sqrt(self.dn)
        self.vol_G = np.sum(self.d)

    def to_shared(self):
        """ Re-create the graph data with multiprocessing compatible
        shared-memory arrays that can be passed to child-processes.

        This returns a dictionary that allows the graph to be
        re-created in a child-process from that variable and
        the method "from_shared"

        At this moment, this doesn't send any data from components,
        core_numbers, or biconnected_components
        """
        sgraphvars = {}
        self.ai, sgraphvars["ai"] = _copy_to_shared(self.ai)
        self.aj, sgraphvars["aj"] = _copy_to_shared(self.aj)
        self.d, sgraphvars["d"] = _copy_to_shared(self.d)
        self.dn, sgraphvars["dn"] = _copy_to_shared(self.dn)
        self.d_sqrt, sgraphvars["d_sqrt"] = _copy_to_shared(self.d_sqrt)
        self.dn_sqrt, sgraphvars["dn_sqrt"] = _copy_to_shared(self.dn_sqrt)
        self.adjacency_matrix.data, sgraphvars["a"] = _copy_to_shared(self.adjacency_matrix.data)

        # this will rebuild without copying
        # so that copies should all be accessing exactly the same
        # arrays for caching
        self.adjacency_matrix = sp.csr_matrix(
            (self.adjacency_matrix.data, self.aj, self.ai),
            shape=(self._num_vertices, self._num_vertices))

        # scalars
        sgraphvars["n"] = self._num_vertices
        sgraphvars["m"] = self._num_edges
        sgraphvars["vol"] = self.vol_G
        sgraphvars["weighted"] = self._weighted

        return sgraphvars

    @classmethod
    def from_shared(cls, sgraphvars):
        """ Return a graph object from the output of "to_shared". """
        g = cls()
        g._num_vertices = sgraphvars["n"]
        g._num_edges = sgraphvars["m"]
        g._weighted = sgraphvars["weighted"]
        g.vol_G = sgraphvars["vol"]
        g.ai = _load_from_shared(*sgraphvars["ai"])
        g.aj = _load_from_shared(*sgraphvars["aj"])
        g.adjacency_matrix = sp.csr_matrix(
            (_load_from_shared(*sgraphvars["a"]), g.aj, g.ai),
            shape=(g._num_vertices, g._num_vertices))
        g.d = _load_from_shared(*sgraphvars["d"])
        g.dn = _load_from_shared(*sgraphvars["dn"])
        g.d_sqrt = _load_from_shared(*sgraphvars["d_sqrt"])
        g.dn_sqrt = _load_from_shared(*sgraphvars["dn_sqrt"])
        return g

    def connected_components(self):
        """
        Computes the connected components of the graph. It stores the results in class attributes components
        and number_of_components. The user needs to call read the graph
        first before calling this function by calling the read_graph function from this class.
        """

        output = csgraph.connected_components(self.adjacency_matrix,directed=False)

        self.components = output[1]
        self.number_of_components = output[0]

        print('There are ', self.number_of_components, ' connected components in the graph')

    def is_disconnected(self):
        """
        The output can be accessed from the graph object that calls this function.

        Checks if the graph is a disconnected graph. It prints the result as a comment and
        returns True if the graph is disconnected, or false otherwise. The user needs to
        call read the graph first before calling this function by calling the read_graph function from this class.
        This function calls Networkx.

        Returns
        -------
        True
             If connected

        False
             If disconnected
        """
        if self.d == []:
            print('The graph has to be read first.')
            return

        self.connected_components()

        if self.number_of_components > 1:
            print('The graph is a disconnected graph.')
            return True
        else:
            print('The graph is not a disconnected graph.')
            return False

    def biconnected_components(self):
        """
        Computes the biconnected components of the graph. It stores the results in class attributes bicomponents
        and number_of_bicomponents. The user needs to call read the graph first before calling this
        function by calling the read_graph function from this class. This function calls Networkx.
        """
        warnings.warn("Warning, biconnected_components is not efficiently implemented.")

        g_nx = nx.from_scipy_sparse_matrix(self.adjacency_matrix)

        self.bicomponents = list(nx.biconnected_components(g_nx))

        self.number_of_bicomponents = len(self.bicomponents)

    def core_number(self):
        """
        Returns the core number for each vertex. A k-core is a maximal
        subgraph that contains nodes of degree k or more. The core number of a node
        is the largest value k of a k-core containing that node. The user needs to
        call read the graph first before calling this function by calling the read_graph
        function from this class. The output can be accessed from the graph object that
        calls this function. It stores the results in class attribute core_numbers.
        """
        warnings.warn("Warning, core_number is not efficiently implemented.")

        g_nx = nx.from_scipy_sparse_matrix(self.adjacency_matrix)

        self.core_numbers = nx.core_number(g_nx)

    def neighbors(self,vertex):
        """
        Returns a list with the neighbors of the given vertex.
        """
        # this will be faster since we store the arrays ourselves.
        return self.aj[self.ai[vertex]:self.ai[vertex+1]].tolist()
        #return self.adjacency_matrix[:,vertex].nonzero()[0].tolist()

    def compute_conductance(self,R,cpp=True):
        """
        Return conductance of a set of vertices.
        """

        records = self.set_scores(R,cpp=cpp)

        return records["cond"]

    def set_scores(self,R,cpp=True):
        """
        Return various metrics of a set of vertices.
        """
        voltrue,cut = 0,0
        if cpp:
            voltrue, cut = set_scores_cpp(self._num_vertices,self.ai,self.aj,self.adjacency_matrix.data,self.d,R,self._weighted)
        else:
            voltrue = sum(self.d[R])
            v_ones_R = np.zeros(self._num_vertices)
            v_ones_R[R] = 1
            cut = voltrue - np.dot(v_ones_R,self.adjacency_matrix.dot(v_ones_R.T))
        voleff = min(voltrue,self.vol_G - voltrue)

        sizetrue = len(R)
        sizeeff = sizetrue
        if voleff < voltrue:
            sizeeff = self._num_vertices - sizetrue

        # remove the stuff we don't want returned...
        del R
        del self
        if not cpp:
            del v_ones_R
        del cpp

        edgestrue = voltrue - cut
        edgeseff = voleff - cut

        cond = cut / voleff if voleff != 0 else 1
        isop = cut / sizeeff if sizeeff != 0 else 1

        # make a dictionary out of local variables
        return locals()

    def largest_component(self):
        self.connected_components()
        if self.number_of_components == 1:
            #self.compute_statistics()
            return self
        else:
            # find nodes of largest component
            counter=cole.Counter(self.components)
            maxccnodes = []
            what_key = counter.most_common(1)[0][0]
            for i in range(self._num_vertices):
                if what_key == self.components[i]:
                    maxccnodes.append(i)

            # biggest component by len of it's list of nodes
            #maxccnodes = max(self.components, key=len)
            #maxccnodes = list(maxccnodes)

            warnings.warn("The graph has multiple (%i) components, using the largest with %i / %i nodes"%(
                     self.number_of_components, len(maxccnodes), self._num_vertices))

            g_copy = GraphLocal()
            g_copy.adjacency_matrix = self.adjacency_matrix[maxccnodes,:].tocsc()[:,maxccnodes].tocsr()
            g_copy._num_vertices = len(maxccnodes) # AHH!
            g_copy.compute_statistics()
            g_copy._weighted = self._weighted
            dt = np.dtype(self.ai[0])
            itype = np.int64 if dt.name == 'int64' else np.uint32
            dt = np.dtype(self.aj[0])
            vtype = np.int64 if dt.name == 'int64' else np.uint32
            g_copy.ai = itype(g_copy.adjacency_matrix.indptr)
            g_copy.aj = vtype(g_copy.adjacency_matrix.indices)
            g_copy._num_edges = g_copy.adjacency_matrix.nnz
            return g_copy

    def local_extrema(self,vals,strict=False,reverse=False):
        """
        Find extrema in a graph based on a set of values.

        Parameters
        ----------

        vals: Sequence[float]
            a feature value per node used to find the ex against each other, i.e. conductance

        strict: bool
            If True, find a set of vertices where vals(i) < vals(j) for all neighbors N(j)
            i.e. local minima in the space of the graph
            If False, find a set of vertices where vals(i) <= vals(j) for all neighbors N(j)
            i.e. local minima in the space of the graph

        reverse: bool
            if True, then find local maxima, if False then find local minima
            (by default, this is false, so we find local minima)

        Returns
        -------

        minverts: Sequence[int]
            the set of vertices

        minvals: Sequence[float]
            the set of min values
        """
        n = self.adjacency_matrix.shape[0]
        minverts = []
        ai = self.ai
        aj = self.aj
        factor = 1.0
        if reverse:
            factor = -1.0
        for i in range(n):
            vali = factor*vals[i]
            lmin = True
            for nzi in range(ai[i],ai[i+1]):
                v = aj[nzi]
                if v == i:
                    continue # skip self-loops
                if strict:
                    if vali < factor*vals[v]:
                        continue
                    else:
                        lmin = False
                else:
                    if vali <= factor*vals[v]:
                        continue
                    else:
                        lmin = False

                if lmin == False:
                    break # break out of the loop

            if lmin:
                minverts.append(i)

        minvals = vals[minverts]

        return minverts, minvals
    
    def draw(self,coords,alpha=1.0,nodesize=5,linewidth=1,
             nodealpha=1.0,edgealpha=0.01,nodecolor='r',
             edgecolor='k',nodemarker='o',setalpha=1.0,
             setcolor='y',axs=None,fig=None,nodeset=None,
             groups=None):
        """
        standard drawing function of GraphLocal object

        Parameters
        ----------

        coords: a n-by-2 or n-by-3 array with coordinates for each node of the graph.

        Optional parameters
        ------------------

        alpha: float (1.0 by default)
            the overall alpha scaling of the plot, [0,1]

        nodealpha: float (1.0 by default)
            the overall node alpha scaling of the plot, [0, 1]

        edgealpha: float (1.0 by default)
            the overall edge alpha scaling of the plot, [0, 1]

        setalpha: float (1.0 by default)
            the overall set alpha scaling of the plot, [0, 1]

        nodecolor: string or RGB ('r' by default)

        edgecolor: string or RGB ('k' by default)

        setcolor: string or RGB ('y' by default)

        nodemarker: string ('o' by default)

        nodesize: float (5.0 by default)

        linewidth: float (1.0 by default)

        nodeset: Sequence[int] (None by default)
            a set of nodes to highlight

        groups: Sequence[Sequence[int]] (None by default)
            node partitions, different colors will be assigned to different groups

        axs,fig: None,None (default) 
            by default it will create a new figure, or this will plot in axs if not None.

        Returns
        -------

        a dictionary with: 
        fig, ax, nodes, edges, setnodes, setedges, groupnodes, groupedges
        these are the handles to the actual plot elements, so that you could change 
        values after the fact. 
        """
        if axs == None:
            fig = plt.figure()
            if len(coords[0]) == 3:
                axs = fig.add_subplot(111, projection='3d')
            else:
                axs = fig.add_subplot(111)
        axs.set_axis_off()
        nodeset = set(nodeset) if nodeset != None else set()
        nodelist_in = []
        nodelist_out = []
        for i in range(self._num_vertices):
            if i in nodeset:
                nodelist_in.append(i)
            else:
                nodelist_out.append(i)
        N = nx.Graph()
        coo = self.adjacency_matrix.tocoo()
        #color, coords and alpha information are stored directly in node attributes
        N.add_nodes_from(list(zip(nodelist_out,[{'pos':coords[i],'alpha':alpha*nodealpha,'color':nodecolor} for i in nodelist_out])))
        N.add_nodes_from(list(zip(nodelist_in,[{'pos':coords[i],'alpha':alpha*setalpha,'color':setcolor} for i in nodelist_in])))
        edge_list = [(coo.row[i],coo.col[i]) for i in range(self._num_edges)]
        #color information is stored directly in edge information
        N.add_edges_from(edge_list,color=edgecolor)
        setedges = []
        for i in range(self._num_edges):
            if coo.row[i] in nodeset and coo.col[i] in nodeset:
                setedges.append((coo.row[i],coo.col[i]))
        
        #reassign node colors based on partition
        groupedges = None
        if groups != None:
            groupedges = [[] for i in range(len(groups))]
            number_of_colors = len(groups)
            color = ["#"+''.join([random.choice('0123456789ABCDEF') for j in range(6)])
                     for i in range(number_of_colors)]
            for i,g in enumerate(groups):
                nx.set_node_attributes(N,{k:{'color':v} for (k,v) in zip(g,[color[i]]*len(g))})
                sg = set(g)
                for j in g:
                    for k in range(self.ai[j],self.ai[j+1]):
                        if self.aj[k] >= j and self.aj[k] in sg:
                            groupedges[i].append((j,self.aj[k]))
                            
        if len(coords[0]) == 3:
            self.draw_nx_3d(N,axs,nodemarker,nodesize,alpha*edgealpha,linewidth)
        else:
            self.draw_nx(N,axs,nodemarker,nodesize,alpha*edgealpha,linewidth)

        ret_dict = {"fig":fig,"ax":axs,"nodes":list(N.nodes),"edges":list(N.edges),
                    "setnodes":nodelist_in,"setedges":setedges,"nx_graph":N,"groupnodes":groups,
                    "groupedges":groupedges}
        return ret_dict
    
    @staticmethod
    def draw_nx(N,axs,nodemarker='o',nodesize=5,edgealpha=0.01,linewidth=1):
        """
        a static method to draw a networkx instance, the plot will be based on node and edge attributes,
        valid node attributes include "alpha", "color" and "pos". valid edge attributes include "color", 
        designed to modify the plot returned by calling "draw" function

        Parameters
        ----------
        N: networkx object

        axs: matplotlib axes

        nodemarker: 'o' by default

        nodesize: 5 by default

        edgealpha: 0.01 by default

        linewidth: 1 by default
        """
        nnodes = nx.number_of_nodes(N)
        node_alpha_list = [0]*nnodes
        node_color_list = ['']*nnodes
        for i,node in enumerate(N.nodes(data=True)):
            node_alpha_list[i] = node[1]['alpha']
            node_color_list[i] = node[1]['color']
        nedges = nx.number_of_edges(N)
        edge_color_list = ['']*nedges
        for i,edge in enumerate(N.edges(data=True)):
            edge_color_list[i] = edge[2]['color']
        nx.draw_networkx_nodes(N,pos=nx.get_node_attributes(N,'pos'),node_size=nodesize,ax=axs,alpha=node_alpha_list,
                               node_color=node_color_list,node_shape=nodemarker)
        nx.draw_networkx_edges(N,pos=nx.get_node_attributes(N,'pos'),ax=axs,edge_color=edge_color_list,alpha=edgealpha,
                              linewidths=linewidth)
    
    @staticmethod
    def draw_nx_3d(N,axs,nodemarker='o',nodesize=5,edgealpha=0.01,linewidth=1,angle=30):
        """
        a static method to draw a networkx instance, the plot will be based on node and edge attributes,
        valid node attributes include "alpha", "color" and "pos". valid edge attributes include "color", 
        designed to modify the plot returned by calling "draw" function

        Parameters
        ----------
        N: networkx object

        axs: matplotlib axes

        nodemarker: 'o' by default

        nodesize: 5 by default

        edgealpha: 0.01 by default
        
        linewidth: 1 by default

        angle: view angle, 30 by default
        """
        pos = nx.get_node_attributes(N,'pos')
        # Loop on the pos dictionary to extract the x,y,z coordinates of each node
        for key, value in N.nodes(data=True):
            coord = value['pos']
            # Scatter plot
            axs.scatter(coord[0],coord[1],coord[2],c=value['color'],alpha=value['alpha'],
                       marker=nodemarker,s=nodesize)

        # Loop on the list of edges to get the x,y,z, coordinates of the connected nodes
        # Those two points are the extrema of the line to be plotted
        for i,edge in enumerate(N.edges(data=True)):
            x = np.array((pos[edge[0]][0], pos[edge[1]][0]))
            y = np.array((pos[edge[0]][1], pos[edge[1]][1]))
            z = np.array((pos[edge[0]][2], pos[edge[1]][2]))

            # Plot the connecting lines
            axs.plot(x,y,z,c=edge[2]['color'],alpha=edgealpha,linewidth=linewidth)
        
        # Set the initial view
        axs.view_init(30, angle)
