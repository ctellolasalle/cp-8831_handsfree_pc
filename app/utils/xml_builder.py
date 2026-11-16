"""Generador de configuración XML Cisco específico para CP-8831 en modo altavoz manos libres."""

import xml.etree.ElementTree as ET
from xml.dom import minidom
import re

def normalize_mac(mac: str) -> str:
    """Limpia y estandariza la dirección MAC en formato mayúsculas sin separadores."""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", mac).upper()
    return cleaned

def generate_cisco_8831_xml(mac: str, pc_ip: str, sip_port: int = 5060, extension: str = "8831",
                            display_name: str = "PC Handsfree", auto_answer: bool = True,
                            transport: str = "1") -> str:
    """
    Genera el archivo SEP<MAC>.cnf.xml para Cisco CP-8831.
    auto_answer: Si es True, autoAnswerEnabled = 2 (Auto Answer con Speakerphone).
    transport: 1 = TCP, 2 = UDP.
    """
    clean_mac = normalize_mac(mac)
    root = ET.Element("device")

    # tzdata para CP-8831
    tzdata = ET.SubElement(root, "tzdata")
    ET.SubElement(tzdata, "tzolsonversion").text = "2019a"
    ET.SubElement(tzdata, "tzupdater").text = "j9-tzdata.jar"

    ET.SubElement(root, "deviceProtocol").text = "SIP"

    # devicePool
    device_pool = ET.SubElement(root, "devicePool")
    dt_setting = ET.SubElement(device_pool, "dateTimeSetting")
    ET.SubElement(dt_setting, "dateTemplate").text = "D/M/Y"
    ET.SubElement(dt_setting, "timeZone").text = "SA Eastern Standard Time"
    ET.SubElement(dt_setting, "olsonTimeZone").text = "America/Argentina/Buenos_Aires"

    ntps = ET.SubElement(dt_setting, "ntps")
    ntp = ET.SubElement(ntps, "ntp")
    ET.SubElement(ntp, "name").text = pc_ip
    ET.SubElement(ntp, "ntpMode").text = "Unicast"

    # callManagerGroup
    cm_group = ET.SubElement(device_pool, "callManagerGroup")
    members = ET.SubElement(cm_group, "members")
    member = ET.SubElement(members, "member", priority="0")
    cm = ET.SubElement(member, "callManager")
    ports = ET.SubElement(cm, "ports")
    ET.SubElement(ports, "ethernetPhonePort").text = "2000"
    ET.SubElement(ports, "sipPort").text = str(sip_port)
    ET.SubElement(ports, "securedSipPort").text = "5061"
    ET.SubElement(cm, "processNodeName").text = pc_ip

    # sipProfile
    sip_profile = ET.SubElement(root, "sipProfile")
    ET.SubElement(sip_profile, "natEnabled").text = "false"
    ET.SubElement(sip_profile, "natAddress").text = ""

    sip_proxies = ET.SubElement(sip_profile, "sipProxies")
    ET.SubElement(sip_proxies, "registerWithProxy").text = "true"
    ET.SubElement(sip_proxies, "outboundProxy").text = ""
    ET.SubElement(sip_proxies, "outboundProxyPort").text = ""
    ET.SubElement(sip_proxies, "backupProxy").text = pc_ip
    ET.SubElement(sip_proxies, "backupProxyPort").text = str(sip_port)

    phone_label = display_name[:12]
    ET.SubElement(sip_profile, "phoneLabel").text = phone_label

    # sipLines
    sip_lines = ET.SubElement(sip_profile, "sipLines")
    line = ET.SubElement(sip_lines, "line", button="1", lineIndex="1")
    ET.SubElement(line, "featureID").text = "9"
    ET.SubElement(line, "featureLabel").text = extension
    ET.SubElement(line, "proxy").text = "USECALLMANAGER"
    ET.SubElement(line, "port").text = str(sip_port)
    ET.SubElement(line, "name").text = extension
    ET.SubElement(line, "displayName").text = display_name

    auto_ans = ET.SubElement(line, "autoAnswer")
    # 2 = Auto Answer con Speakerphone (Altavoz manos libres inmediato)
    # 0 = Desactivado (timbrado estándar)
    ET.SubElement(auto_ans, "autoAnswerEnabled").text = "2" if auto_answer else "0"

    ET.SubElement(line, "callWaiting").text = "3"
    ET.SubElement(line, "authName").text = extension
    ET.SubElement(line, "authPassword").text = ""
    ET.SubElement(line, "sharedLine").text = "false"
    ET.SubElement(line, "messageWaitingLampPolicy").text = "1"
    ET.SubElement(line, "messagesNumber").text = extension
    ET.SubElement(line, "ringSettingIdle").text = "4"
    ET.SubElement(line, "ringSettingActive").text = "5"
    ET.SubElement(line, "contact").text = extension

    fwd = ET.SubElement(line, "forwardCallInfoDisplay")
    ET.SubElement(fwd, "callerName").text = "true"
    ET.SubElement(fwd, "callerNumber").text = "false"
    ET.SubElement(fwd, "redirectedNumber").text = "false"
    ET.SubElement(fwd, "dialedNumber").text = "true"

    ET.SubElement(sip_profile, "dialTemplate").text = "dialplan.xml"

    # vendorConfig
    vendor_cfg = ET.SubElement(root, "vendorConfig")
    ET.SubElement(vendor_cfg, "disableSpeaker").text = "false"
    ET.SubElement(vendor_cfg, "disableSpeakerAndHeadset").text = "false"
    ET.SubElement(vendor_cfg, "autoSelectLineEnable").text = "0"
    ET.SubElement(vendor_cfg, "settingsAccess").text = "1"
    ET.SubElement(vendor_cfg, "webAccess").text = "0"  # Habilitar web interface
    ET.SubElement(vendor_cfg, "webProtocol").text = "0"
    ET.SubElement(vendor_cfg, "webAdmin").text = "1"

    # userLocale / networkLocale (Español)
    user_loc = ET.SubElement(root, "userLocale")
    ET.SubElement(user_loc, "name").text = "Spanish_Spain"
    ET.SubElement(user_loc, "langCode").text = "es_ES"
    ET.SubElement(root, "networkLocale").text = "Spain"
    net_info = ET.SubElement(root, "networkLocaleInfo")
    ET.SubElement(net_info, "name").text = "Spain"

    # 1=TCP, 2=UDP
    ET.SubElement(root, "transportLayerProtocol").text = str(transport)

    rough = ET.tostring(root, encoding="utf-8")
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")

def generate_dialplan_xml() -> str:
    return """<DIALTEMPLATE>
  <TEMPLATE MATCH="*" Timeout="3" User="Phone"/>
</DIALTEMPLATE>"""

