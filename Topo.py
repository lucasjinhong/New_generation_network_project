"""Custom topology example

Two directly connected switches plus a host for each switch:

   host --- switch --- switch --- host

Adding the 'topos' dict with a key/value pair to generate our newly defined
topology enables one to pass in '--topo=mytopo' from the command line.
"""

from mininet.topo import Topo
from mininet.node import CPULimitedHost, Host, Node

class MyTopo( Topo ):
    def build( self ):
        # Add hosts and switches
        
        host_1 = self.addHost( 'h1', cls=Host, ip='10.0.0.1', defaultRoute=None, mac='00:00:00:00:00:01' )
        host_2 = self.addHost( 'h2', cls=Host, ip='10.0.0.2', defaultRoute=None, mac='00:00:00:00:00:02' )

        switch_1 = self.addSwitch( 's1' )
        switch_2 = self.addSwitch( 's2' )
        switch_3 = self.addSwitch( 's3' )
        switch_4 = self.addSwitch( 's4' )
        switch_5 = self.addSwitch( 's5' )

        # Add links
        self.addLink( switch_1, switch_2 )
        self.addLink( switch_1, switch_3 )
        self.addLink( switch_3, switch_4 )
        self.addLink( switch_3, switch_5 )
        self.addLink( switch_5, switch_4 )

        self.addLink( switch_1, host_1 )
        self.addLink( switch_4, host_2 )

topos = { 'mytopo': ( lambda: MyTopo() ) }