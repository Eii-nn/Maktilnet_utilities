:delay 15s

/user set [ find name=admin ] password=adminapollo group=full
/ip service set ssh disabled=no
/ip service set telnet disabled=yes
/interface bridge add fast-forward=no name=loopback0
/interface ethernet set [ find default-name=ether1 ] comment=SECONDARY-CIRCUIT name=port1
/interface ethernet set [ find default-name=ether2 ] comment=PRIMARY-CIRCUIT name=port2

/ip route add gateway=192.168.8.1 dst-address=0.0.0.0/0 distance=10
/ip route add gateway=192.168.9.1 dst-address=0.0.0.0/0 distance=20

/ip dhcp-client add interface=port1 disabled=no add-default-route=yes
/ip dhcp-client add interface=port2 disabled=no add-default-route=yes

/ip address add address=192.168.8.100/24 network=192.168.8.0 interface=port1
/ip address add address=192.168.9.100/24 network=192.168.9.0 interface=port2
/ip dns set allow-remote-requests=yes servers=8.8.8.8,1.1.1.1

/interface ovpn-client add cipher=aes256-gcm connect-to=preflightvpn.apolloglobal.net port=1194 mode=ip name=preflight-ovpn password=PREfl1ght@ovpn@2k26Access user=D3D90D61688E certificate=none verify-server-certificate=no auth=sha1 use-peer-dns=yes add-default-route=no

/snmp set enabled=yes
/snmp community add name=preflight-snmp read-access=yes addresses=192.168.18.111







