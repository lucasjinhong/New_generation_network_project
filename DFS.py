from collections import defaultdict
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.topology import event
from ryu.controller.handler import MAIN_DISPATCHER,CONFIG_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.topology.api import get_switch,get_all_link,get_link
import copy
import random

class Topo(object):
    def __init__(self,logger):
        self.adjacent=defaultdict(lambda s1s2:None)
        self.switches=None
        self.host_mac_to={}
        self.logger=logger    
    
    def get_adjacent(self,s1,s2):
        return self.adjacent.get((s1,s2))
    
    def set_adjacent(self,s1,s2,port,weight):
        self.adjacent[(s1,s2)]=(port,weight)
    
    def findpath(self,src_sw,dst_sw,sign,onepath,allpaths):
        if src_sw==dst_sw:
            allpaths.append(onepath.copy())
        else:
            for i in self.switches:
                if (self.get_adjacent(src_sw,i) is not None) and (sign[i]!=1):
                    sign[i]=1
                    onepath.append(i)

                    self.findpath(i,dst_sw,sign,onepath,allpaths)

                    onepath.remove(i)
                    sign[i]=0
                         
    def shortest_path(self,src_sw,dst_sw,first_port,last_port):
        self.logger.info("topo calculate the shortest path from ---{}-{}-------{}-{}".format(first_port,src_sw,dst_sw,last_port))
        self.logger.debug("there is {} swithes".format(len(self.switches)))
        
        sign={}
        for s in self.switches:
            sign[s]=0
        sign[src_sw]=1
        
        onepath=[]
        onepath.append(src_sw)

        allpaths=[]
        self.findpath(src_sw,dst_sw,sign,onepath,allpaths)

        print("paths num is: {}".format(len(allpaths)))
        print("all paths:")
        sp=allpaths[0]
        lp=allpaths[0]
        for i in allpaths:
            if(len(i)>len(lp)):
                lp=i
            if(len(i)<len(sp)):
                sp=i
            print(i)

        print("the shortest path is: ")
        print(sp)
        print("the longest path is: ")
        print(lp)

        if src_sw==dst_sw:
            path=[src_sw]
        else:
            path=lp
            
        record=[]
        inport=first_port

        for s1,s2 in zip(path[:-1],path[1:]):
            outport,_=self.get_adjacent(s1,s2)
                
            record.append((s1,inport,outport))
            inport,_=self.get_adjacent(s2,s1)
            
        record.append((dst_sw,inport,last_port))

        return record

class DijkstraController(app_manager.RyuApp):
    OFP_VERSIONS=[ofproto_v1_3.OFP_VERSION]

    def __init__(self,*args,**kwargs):
        super(DijkstraController,self).__init__(*args,**kwargs)
        self.mac_to_port={}
        # logical switches
        self.datapaths=[]
        #ip ->mac
        self.arp_table={}

        self.topo=Topo(self.logger)
        self.flood_history={}

        self.arp_history={}
    
    def _find_dp(self,dpid):
        for dp in self.datapaths:
            if dp.id==dpid:
                return dp
        return None    

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
    
    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)


    def configure_path(self,shortest_path,event,src_mac,dst_mac):
        #configure shortest path to switches
        msg=event.msg
        datapath=msg.datapath

        ofproto=datapath.ofproto

        parser=datapath.ofproto_parser

        # enumerate the calculated path
        # (s1,inport,outport)->(s2,inport,outport)->...->(dest_switch,inport,outport)
        for switch,inport,outport in shortest_path:
            match=parser.OFPMatch(in_port=inport,eth_src=src_mac,eth_dst=dst_mac)

            actions=[parser.OFPActionOutput(outport)]


            datapath=self._find_dp(int(switch))
            assert datapath is not None

            inst=[parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,actions)]

            #idle and hardtimeout set to 0,making the entry permanent
            #reference openflow spec
            mod=datapath.ofproto_parser.OFPFlowMod(
                datapath=datapath,
                match=match,
                idle_timeout=0,
                hard_timeout=0,
                priority=1,
                instructions=inst
            )
            datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn,MAIN_DISPATCHER)
    def packet_in_handler(self,event):
        msg=event.msg
        datapath=msg.datapath
        ofproto=datapath.ofproto
        parser=datapath.ofproto_parser

        # through which port the packet comes in
        in_port=msg.match['in_port']

        pkt=packet.Packet(msg.data)
        eth=pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype==ether_types.ETH_TYPE_LLDP:
            return
        
        dst_mac=eth.dst
        src_mac=eth.src

        dpid=datapath.id

        self.mac_to_port.setdefault(dpid,{})
        self.mac_to_port[dpid][src_mac]=in_port

        if src_mac not in self.topo.host_mac_to.keys():
            self.topo.host_mac_to[src_mac]=(dpid,in_port)
        
        if dst_mac in self.topo.host_mac_to.keys():
            final_port=self.topo.host_mac_to[dst_mac][1]
            src_switch=self.topo.host_mac_to[src_mac][0]
            dst_switch=self.topo.host_mac_to[dst_mac][0]

            #calculate the shortest path
            shortest_path=self.topo.shortest_path(
                src_switch,
                dst_switch,
                in_port,
                final_port)
            
            self.logger.info("The longest path from {} to {} contains {} switches".format(src_mac,dst_mac,len(shortest_path)))
            
            assert len(shortest_path)>0
            
            path_str=''

            # (s1,inport,outport)->(s2,inport,outport)->...->(dest_switch,inport,outport)
            for s,ip,op in shortest_path:
                path_str=path_str+"--{}-{}-{}--".format(ip,s,op)

            self.logger.info("The longset path from {} to {} is {}".format(src_mac,dst_mac,path_str))
            self.logger.info("Have calculated the longest path from {} to {}".format(src_mac,dst_mac))
            self.logger.info("Now configuring switches of interest")
            self.configure_path(shortest_path,event,src_mac,dst_mac)
            self.logger.info("Configure done\n")

            # current_switch=None
            out_port=None
            for s,_,op in shortest_path:
                if s==dpid:
                    out_port=op
        else: 
            out_port=ofproto.OFPP_FLOOD

        # actions= flood or some port
        actions=[parser.OFPActionOutput(out_port)]

        data=None

        if msg.buffer_id==ofproto.OFP_NO_BUFFER:
            data=msg.data
        
        # send the packet out to avoid packet loss
        out=parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )
        datapath.send_msg(out)

    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter_handler(self,event):
        self.logger.info("A switch entered.Topology rediscovery...")
        self.switch_status_handler(event)
        self.logger.info('Topology rediscovery done')
    
    @set_ev_cls(event.EventSwitchLeave)
    def switch_leave_handler(self,event):
        self.logger.info("A switch leaved.Topology rediscovery...")
        self.switch_status_handler(event)
        self.logger.info('Topology rediscovery done')

    def switch_status_handler(self,event):
        all_switches=copy.copy(get_switch(self,None))

        self.topo.switches=[s.dp.id for s in all_switches]
        self.logger.info("switches {}".format(self.topo.switches))

        self.datapaths=[s.dp for s in all_switches]

        # get link and get port
        all_links=copy.copy(get_link(self,None))
        all_link_stats=[(l.src.dpid,l.dst.dpid,l.src.port_no,l.dst.port_no) for l in all_links]
        self.logger.info("Number of links {}".format(len(all_link_stats)))

        all_link_repr=''

        for s1,s2,p1,p2 in all_link_stats:
            weight=random.randint(1,10)

            self.topo.set_adjacent(s1,s2,p1,weight)
            self.topo.set_adjacent(s2,s1,p2,weight)

            all_link_repr+='s{}p{}--s{}p{}\n'.format(s1,p1,s2,p2)
            
        self.logger.info("All links:\n"+all_link_repr)