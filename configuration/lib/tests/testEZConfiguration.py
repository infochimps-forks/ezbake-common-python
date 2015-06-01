#   Copyright (C) 2013-2014 Computer Sciences Corporation
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from pkg_resources import resource_filename
import os
import jprops
import nose.tools as nt

from ezbake.configuration.EzConfiguration import EzConfiguration
from ezbake.configuration.loaders.PropertiesConfigurationLoader import PropertiesConfigurationLoader
from ezbake.configuration.loaders.DirectoryConfigurationLoader import DirectoryConfigurationLoader
from ezbake.configuration.loaders.OpenShiftConfigurationLoader import OpenShiftConfigurationLoader



conf = resource_filename('tests', 'config/ezbake-config.properties')
with open(conf) as fp:
    properties = jprops.load_properties(fp)


def testEZConfiguration():
    config = EzConfiguration(PropertiesConfigurationLoader(properties))
    nt.eq_(properties.get('application.name', ''),
           config.getProperties().get('application.name',''))


def testLoadFromPackage():
    config = EzConfiguration(DirectoryConfigurationLoader(__name__), PropertiesConfigurationLoader(properties), OpenShiftConfigurationLoader())
    nt.eq_(properties.get('application.name', ''),
           config.getProperties().get('application.name'))

def testLoadFromDefaults():
    #set os env
    os.environ['EZCONFIGURATION_DIR'] = resource_filename(__name__, 'config')

    config = EzConfiguration()
    nt.eq_(properties.get('application.name', ''),
           config.getProperties().get('application.name'))

def testSetAndGet():
    config = EzConfiguration(PropertiesConfigurationLoader({'TestKey' : 'TestValue'}))
    nt.eq_('TestValue', config.getProperties().get('TestKey'))

