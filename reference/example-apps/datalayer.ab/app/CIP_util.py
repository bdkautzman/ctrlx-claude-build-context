import logging
import typing
import pprint
import csv
import re
from pathlib import Path
from app.CIP_provider_node import CIPnode,CIPnodeBulk

def tagSorter(_datalayerPath:str, _controllerPath:str,_tag:object) -> typing.List:
    """
    Sorts top level tag into sub tags.
        :param _tag: = Top level tag object from EIP_client get_tag_info()
        :param _datalayerPath: = Tag path in ctrlX datalayer
        :param _controllerPath: = Tag path on AB controller
        :return tagList: = Tuple list of tags including datalayer path, controller path and type
    """   
    tagList = []

    # Search for array index in datalayer path
    res = re.findall(r'\[.*?\]', _datalayerPath)
    i = ''
    if res != []:
        i = str(res[0]).replace('[','')
        i = i.replace(']','')
        # By the time this is called, there should be only one array value in brackets
        if len(res) > 1:
            myLogger("Failed parsing assertion. Too many brackets in datalayer path.", logging.WARNING, source=__name__)
           
    # Reformat datalayer path
    _datalayerPath = _datalayerPath.replace('[', '/')
    _datalayerPath = _datalayerPath.replace(']','/')
    if _datalayerPath.endswith('/'):
        _datalayerPath = _datalayerPath.rstrip('/')

    if i != '':            
        index = int(i)
    else:
        index = 0

    if 'dimensions' in _tag:
        tag_type = _tag['tag_type']
        #index = 0
        if _tag["dimensions"][0] != 0:
            if res == []:
                for x in range(_tag["dimensions"][0]):
                    tagSortDimensional(tagList, index, _datalayerPath, _controllerPath, _tag) 
                    index = index + 1        
            else:
                # If an array index was provided, only add specified index tags
                tagSortDimensional(tagList,index,_datalayerPath,_controllerPath,_tag, True)
        else:
            tagSortNondimensional(tagList,_datalayerPath,_controllerPath,_tag)                               
    elif 'array' in _tag:      
        # if i != '':            
        #     index = int(i)
        # else:
        #     index = 0
        if _tag["array"] != 0:
            # If no index provided, add all array tags. Check parsed result
            if res == []:
                for x in range(_tag["array"]):
                    tagSortDimensional(tagList, index, _datalayerPath, _controllerPath, _tag)
                    index = index + 1
            else:
                # If an array index was provided, only add specified index tags
                tagSortDimensional(tagList,index,_datalayerPath,_controllerPath,_tag, True)
        else:
            tagSortNondimensional(tagList,_datalayerPath,_controllerPath,_tag)                  
    if 'bit' in _tag:
        tagSortBit(_tag, _datalayerPath,_controllerPath, tagList)

    return tagList

def tagSortDimensional(_tagList:list, _index:int, _datalayerPath:str, _controllerPath:str, _tag:object, _specific:bool = False):
    tag_type = _tag['tag_type']   
    if tag_type == 'atomic':
        dataType = _tag['data_type']
        if _specific:
            abTagTuple = (_datalayerPath, _controllerPath, dataType)
        else:
            abTagTuple = (_datalayerPath + '/' + str(_index), _controllerPath + '[' + str(_index) + ']' , dataType)
        _tagList.append(abTagTuple)
    elif tag_type == 'struct' and _tag['data_type_name'] != 'STRING':
        for attribute in _tag['data_type']['attributes']:
            if _specific:
                tagTupleList = tagSorter(_datalayerPath + '/' + attribute, _controllerPath + '.' + attribute, _tag['data_type']['internal_tags'][attribute])
            else:
                tagTupleList = tagSorter(_datalayerPath + "/" + str(_index) + '/' + attribute, _controllerPath + "[" + str(_index) + '].' + attribute, _tag['data_type']['internal_tags'][attribute])
            for tagTuple in tagTupleList:
                _tagList.append(tagTuple)
    elif tag_type == 'struct' and _tag['data_type_name'] == 'STRING':
        dataType = 'STRING'
        if _specific:
            abTagTuple = (_datalayerPath, _controllerPath, dataType)
        else:
            abTagTuple = (_datalayerPath + '/' + str(_index), _controllerPath + '[' + str(_index) + ']', dataType)
        _tagList.append(abTagTuple)

def tagSortNondimensional(_tagList:list, _datalayerPath:str, _controllerPath:str, _tag:object):
    tag_type = _tag['tag_type'] 
    if tag_type == 'atomic':
            dataType = _tag['data_type']
            abTagTuple = (_datalayerPath, _controllerPath, dataType)
            _tagList.append(abTagTuple)
    elif tag_type == 'struct' and _tag['data_type_name'] != 'STRING' :
        for attribute in _tag['data_type']['attributes']:
            tagTupleList = tagSorter(_datalayerPath + "/" + attribute, _controllerPath + "." + attribute, _tag['data_type']['internal_tags'][attribute])
            for tagTuple in tagTupleList:
                _tagList.append(tagTuple)
    elif tag_type == 'struct' and _tag['data_type_name'] == 'STRING':
            dataType = 'STRING'
            abTagTuple = (_datalayerPath, _controllerPath, dataType)
            _tagList.append(abTagTuple)  

def tagSortBit(_tag, _datalayerPath, _controllerPath, _tagList):
    tag_type = _tag['tag_type']               
    if tag_type == 'atomic':
        dataType = _tag['data_type']
        abTagTuple = (_datalayerPath, _controllerPath, dataType)
        _tagList.append(abTagTuple)

def writeSortedTagsToCSV(_tag:typing.Tuple[object,str], _tagListPath:str) -> typing.List:
    """
    Wrapper for tagSorter function which writes out tag list to csv
        :param _tag: = Tuple containing top level tag object from EIP_client get_tag_info() and tag path in AB controller
        :param _tagListPath: = File path to tag list csv
        :return _EIP_client: = LogixDriver
    """
    tagObject = _tag[0]
    datalayerPath = _tag[1]
    # Replace all path attributes . with /
    datalayerPath = datalayerPath.replace('.','/')

    # If tag name exists, tag object is top level 
    if 'tag_name' in tagObject:
        #abList = tagSorter(tagObject['tag_name'], tagObject['tag_name'], tagObject)
        abList = tagSorter(datalayerPath, _tag[1], tagObject)
    else:      
        # Replace all path array brackets [] with /
        datalayerPath = datalayerPath.replace('[','/')
        datalayerPath = datalayerPath.replace(']','')
        # If path ends in an array value, provide this to tag sorter
        if _tag[1].endswith(']'):
            start = _tag[1].rindex('[')
            end = _tag[1].rindex(']')
            arrayIndex = _tag[1][start:end+1]
            datalayerPathStart = datalayerPath.rindex('/')
            datalayerPath = datalayerPath[:datalayerPathStart] + arrayIndex
        abList = tagSorter(datalayerPath, _tag[1], tagObject)

    # Append or create new tag list at path
    if(Path.exists(Path(_tagListPath))):
        File_object = open(Path(_tagListPath), "a", newline='')
    else:
        File_object = open(Path(_tagListPath), "w+", newline='')

    # Write all tags to list
    writer = csv.writer(File_object)
    for line in abList:
        writer.writerow(line)
    File_object.close()
    return abList

def myLogger(message, level, source=None):
    if (level > logging.INFO): 
        print(message, flush=True)
    if (source == None):    
        logger = logging.getLogger(__name__)
    else:
        logger = logging.getLogger(source)    
    if (level == logging.INFO):
        logger.info(message)
    elif (level == logging.DEBUG):
        logger.debug(message)
    elif (level == logging.WARNING):
        logger.warning(message)
    elif (level == logging.ERROR):
        logger.error(message)

class DuplicateFilter(logging.Filter):

    def filter(self, record):
        # add other fields if you need more granular comparison, depends on your app
        current_log = (record.module, record.levelno, record.msg)
        if current_log != getattr(self, "last_log", None):
            self.last_log = current_log
            return True
        return False

def addData(_tag, _ctrlxDatalayerProvider, _controller):
    myLogger('adding tag: ' + _tag[1], logging.INFO, source=__name__)

    # Format path based on controller scope
    datalayerPath = formatDatalayerPath(_tag, _controller)

    # Create datalayer node
    ABNode = CIPnode(_ctrlxDatalayerProvider,
                _tag[1],
                _controller.plc,
                _tag[2],
                datalayerPath)
    
    # Register datalayer node with provider
    ABNode.register_node()
    return ABNode

def addDataBulk(_tag, _ctrlxDatalayerProvider, _controller, _tagData:list, _index):
    myLogger('adding tag: ' + _tag[1], logging.INFO, source=__name__)

    # Format path based on controller scope
    datalayerPath = formatDatalayerPath(_tag, _controller)

    # Create datalayer node
    ABNode = CIPnodeBulk(_ctrlxDatalayerProvider,
                        _tag[1],
                        _controller.plc,
                        _tag[2],
                        datalayerPath,
                        _tagData,
                        _index)
    
    # Register datalayer node with provider
    ABNode.register_node()
    return ABNode

def formatDatalayerPath(_tag:object, _controller:object) -> str:
    """
    Formats datalayer path using controller info and tag scope
        :param _tag: = Tuple containing top level tag object from EIP_client get_tag_info() and tag path in AB controller
        :param _controller: = Controller object containing PLC and EIP_client
        :return _datalayerPath: = Path string for datalayer provision
    """
    corePath = _tag[0]
    _datalayerPath = _controller.EIP_client.info["product_name"].replace("/", "--").replace(" ","_") + "/" + _controller.ip + "/"
    if corePath.find("Program:") != -1:
        corePath = corePath.replace("Program:", "")
        _datalayerPath += corePath
    else:
        _datalayerPath += "ControllerTags" + "/" + _tag[0]
    return _datalayerPath