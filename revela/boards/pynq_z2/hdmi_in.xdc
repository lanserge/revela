# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
## hdmi_in: the HDMI-in jack's pins and its link clock. Package pins from
## TUL's PYNQ-Z2 master XDC (v1.0). The front end makes TMDS and
## hdmi_hpd; the design makes the two DDC ports, where it wires the
## receiver's DDC slave.
set_property -dict { PACKAGE_PIN N18 IOSTANDARD TMDS_33 } [get_ports TMDS_clk_p]
set_property -dict { PACKAGE_PIN P19 IOSTANDARD TMDS_33 } [get_ports TMDS_clk_n]
set_property -dict { PACKAGE_PIN V20 IOSTANDARD TMDS_33 } [get_ports {TMDS_data_p[0]}]
set_property -dict { PACKAGE_PIN W20 IOSTANDARD TMDS_33 } [get_ports {TMDS_data_n[0]}]
set_property -dict { PACKAGE_PIN T20 IOSTANDARD TMDS_33 } [get_ports {TMDS_data_p[1]}]
set_property -dict { PACKAGE_PIN U20 IOSTANDARD TMDS_33 } [get_ports {TMDS_data_n[1]}]
set_property -dict { PACKAGE_PIN N20 IOSTANDARD TMDS_33 } [get_ports {TMDS_data_p[2]}]
set_property -dict { PACKAGE_PIN P20 IOSTANDARD TMDS_33 } [get_ports {TMDS_data_n[2]}]
# DDC: served by ddc_slave now (EDID + registers), not dvi2rgb. The
# real pull-ups are the source's (the Pi carries them); the weak
# internal ones only keep the bus from floating with no cable in.
set_property -dict { PACKAGE_PIN U14 IOSTANDARD LVCMOS33 PULLUP true } [get_ports ddc_scl_io]
set_property -dict { PACKAGE_PIN U15 IOSTANDARD LVCMOS33 PULLUP true } [get_ports ddc_sda_io]
set_property -dict { PACKAGE_PIN T19 IOSTANDARD LVCMOS33 } [get_ports hdmi_hpd]
## 148.5 MHz: the frontend is CONSTRAINED for its fastest legal link
## (1080p), as the board's base overlay does; slower actual links (our
## 720p60) are handled by the IP at runtime. Constraining at the actual
## 74.25 puts the MMCM VCO below its floor and DRC rightly refuses.
create_clock -period 6.734 -name tmds_clk [get_ports TMDS_clk_p]
