#!/usr/bin/env python
import sys
import os
import argparse

from MachoRebuilder import bitcode_build_tool_main
from MachoRebuilder import ENV 
from MachoRebuilder import CommandTool
from MachoRebuilder import MachoType
from MachoRebuilder import Xar

import xml.etree.ElementTree as ET

def parse_args(args):
    """Get the command line arguments, and make sure they are correct."""

    parser = argparse.ArgumentParser(
        description="Recompile MachO from bitcode.", )

    # Start args unique for this wrapper
    parser.add_argument("input_static_lib", type=str,
                        help="The input archived static library file")
    parser.add_argument("--wdir", type=str, required=True,
                        help="Working directory for processing the static lib")
    # End args unique for this wrapper 

    parser.add_argument("-o", "--output", type=str, dest="output",
                        default="a.out", help="Output file")
    parser.add_argument("-L", "--library", action="append", dest="include",
                        default=[], help="Dylib search path")
    parser.add_argument("-t", "--tool", action="append", dest="tool_path",
                        default=[], help="Additional tool search path")
    parser.add_argument("--sdk", type=str, dest="sdk_path",
                        help="SDK path")
    parser.add_argument("--generate-dsym", type=str, dest="dsym_output",
                        help="Generate dSYM for the binary and output to path")
    parser.add_argument("--library-list", type=str, dest="library_list",
                        help="A list of dynamic libraries to link against")
    parser.add_argument("--symbol-map", type=str, dest="symbol_map",
                        help="bcsymbolmap file or directory")
    parser.add_argument("--strip-swift-symbols", action="store_true",
                        dest="strip_swift", help="Strip out Swift symbols")
    parser.add_argument("--translate-watchos", action="store_true",
                        dest="translate_watchos", help="translate armv7k watch app to arm64_32")
    parser.add_argument("--save-temps", action="store_true", dest="save_temp",
                        help="leave all the temp directories behind")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--verify", action="store_true",
                        help="Verify the bundle without compiling")
    parser.add_argument("-j", "--threads", metavar="N", type=int,
                        default=1, dest="j",
                        help="How many jobs to execute at once. (default=1)")
    parser.add_argument("--liblto", type=str, dest="liblto", default=None,
                        help="libLTO.dylib path to overwrite the default")
    parser.add_argument("--xml", type=str, dest="use_xml", default=None,
                        help="XML path to overwrite the default in bitcode")
    parser.add_argument("--compile-swift-with-clang", action="store_true",
                        dest="compile_with_clang", help=argparse.SUPPRESS)

    args = parser.parse_args(args[1:])

    return args

class TransArgrument(object):

    """ Reprocessing args to inner module """

    def __init__(self):
        self.fwd_args = []

    def append_kv_args(self, tuple_list): 
        """ Forward tuple args like (k, v), expand v if it is a list. """
        fwd_args = self.fwd_args
        for (key, value) in tuple_list:
            if key and value:
                if type(value) == list:
                    for v in value:
                        fwd_args.extend([key, v])
                else:
                    fwd_args.extend([key, value])
    
    def append_single_args(self, arg_list):
        """ Forward single args """
        fwd_args = self.fwd_args
        for value in arg_list:
            if value:
                fwd_args.append(value)
    
    def get(self):
        """ getter of assembled arg list """
        return self.fwd_args

def pre_condition(condition, msg):
    """ Wrapper for condition """
    if not condition:
        ENV.error(msg)

def post_condition(condition, msg): 
    """ Wrapper for condition """
    if not condition:
        ENV.error(msg)

class LibTransformer(object):

    """ The transformer for all """

    def __init__(self, input_path, arch, args, working_base):
        self.input_path = input_path
        self.arch = arch
        self.working_base = working_base
        self.args = args
    
        self.name = os.path.basename(input_path)
        self.arch_dir = None
        self.objs_dir = None

        self.thin_file_path = None
        self.expected_obj_name = None
        self.expected_obj_path = None
        self.xar_path = None


    def __create_arch_dir_in(self, base):
        pre_condition(self.arch is not None, u"Missing arch")
        pre_condition(base is not None, u"Missing working base")

        arch_dir = os.path.join(base, self.arch)
        if not os.path.isdir(arch_dir):
            os.makedirs(arch_dir)
        self.arch_dir = arch_dir

    def __create_objs_dir_in(self, base): 
        pre_condition(os.path.exists(base), 
                    u"Arch dir not exist: {}.".format(base)) 
        objs_dir = os.path.join(base, "objs")
        if not os.path.isdir(objs_dir):
            os.makedirs(objs_dir)
        self.objs_dir = objs_dir
        
    def __make_name(self, postfix):
        """ make arch specific names """
        return ''.join([self.name, "-", self.arch, postfix])
    
    def __gen_thin_file_in(self, base): 
        """ """
        pre_condition(os.path.exists(base), 
                            u"Arch dir not exist. {}".format(base))

        thin_file_path = os.path.join(base, 
                            self.__make_name(".thin"))
        extract_job = CommandTool.ExtractSlice(self.input_path, 
                                    self.arch, thin_file_path).run() 

        post_condition(extract_job.returncode == 0,
            u"Cannot extract arch {} from {}".format(self.arch, self.input_path))
        self.thin_file_path = thin_file_path
    
    def __unarchive_thinned_lib_in(self, dest_dir):
        """ """
        pre_condition(os.path.exists(self.thin_file_path), 
                            u"Thin file path not exist. {}".format(self.thin_file_path))

        unarchive_job = CommandTool.UnarchiveStaticLib(self.thin_file_path, dest_dir).run()
        
        post_condition(unarchive_job.returncode == 0,
            u"Cannot unarchive from {}".format(self.thin_file_path))

    def __extract_xar_to_dir(self, objs_dir):
        """ extract xar from the master object file """
        pre_condition(os.path.exists(objs_dir),
            u"Objs dir not exist {}".format(objs_dir))
            
        expected_obj_name = self.__make_name('-master.o')
        expected_obj_path = os.path.join(objs_dir, expected_obj_name)

        pre_condition(os.path.exists(expected_obj_path),
            u"Cannot find expected object file {} after extracting".format(expected_obj_path))
        
        # extract xar for arch from thinned .o file
        xar_name = ''.join([expected_obj_name, '.xar'])
        xar_path = os.path.join(self.arch_dir, xar_name)
        extract_xar = CommandTool.ExtractXAR(expected_obj_path, xar_path).run()

        post_condition(extract_xar.returncode == 0,
            u"Cannot extract bundle from {} ({})".format(expected_obj_path, self.arch))
        self.expected_obj_name = expected_obj_name
        self.expected_obj_path = expected_obj_path
        self.xar_path = xar_path 

    def __extract_xml_from(self, xar_path):
        """ build a ElementTree from xar file """
        pre_condition(os.path.exists(xar_path),
            u"Xar file not exist {}".format(xar_path))

        # extract xml for arch from .xar file
        root_elmt = Xar(xar_path).root_doc() 

        post_condition(root_elmt is not None,
            u"Construct XML Tree from {} ({})".format(xar_path, self.arch))
        return root_elmt

    def __patch_element_tree(self, root_elmt):
        """ patch the element tree """
        # patch platform in subdoc
        platform_elmt = root_elmt.find("./subdoc/platform")
        if platform_elmt.text == 'Unknown':
            platform_elmt.text = 'iOS' 

        # patch clang options
        for file_elmt in root_elmt.findall("./toc/file"):
            fid = file_elmt.get('id')
            fname = file_elmt.find('name').text
            clang_options = file_elmt.find('clang')
            for cmd in clang_options.findall('cmd'):
                if cmd.text == "-disable-llvm-passes":
                    clang_options.remove(cmd) 
            # build option covert, based on fname
            sub = ET.SubElement(clang_options, "cmd")
            sub.text = "-mllvm"
            sub = ET.SubElement(clang_options, "cmd")
            sub.text = "-fla"

        xml_name = ''.join([self.expected_obj_name, '.xml'])
        xml_path = os.path.join(self.arch_dir, xml_name)
        with open(xml_path, 'w') as f:
            xml_string = ET.tostring(root_elmt)
            f.write(xml_string)

        self.xml_path = xml_path
    
    def __forward_to_obfuscation(self, args):
        """ """
        obf_obj_path = os.path.join(self.arch_dir, self.__make_name('-master-p.o'))

        fwd_args = TransArgrument()    
        fwd_args.append_single_args(["main.py", self.expected_obj_path]) 
        fwd_args.append_kv_args([("-t", args.tool_path),  
                                ("--xml", self.xml_path),
                                ("-o", obf_obj_path)]);
        fwd_args.append_single_args(["-v"])

        ENV.log(u"Forward arguments to internal build tool: {}".format(fwd_args))
        self.obf_obj_path = obf_obj_path

        bitcode_build_tool_main(fwd_args.get())
        post_condition(os.path.exists(obf_obj_path), 
            u"Cannot find transformed object file {} ({})".format(self.obf_obj_path, self.arch))

    def __archive_objs(self, objs_dir, include_list =[], exclude_list = []):
        """ """
        # gather other objects in objs dir (exclude original object file)
        other_objs = []
        for file in os.listdir(objs_dir):
            if file.endswith(".o") and file not in exclude_list:
                other_objs.append(os.path.join(objs_dir, file))

        obf_achv_path = os.path.join(self.arch_dir, self.__make_name('_obfuscated.a'))
        all_objs_list = include_list + other_objs
        archive_job = CommandTool.AssembleStaticLib(all_objs_list, obf_achv_path, self.working_base).run()

        post_condition(archive_job.returncode == 0, 
            u"Cannot archive target {}".format(obf_achv_path))
        self.obf_achv_path = obf_achv_path

    def run(self):
        """ running the whole process to get $name-$arch_obfuscated.a """
        self.__create_arch_dir_in(self.working_base)
        self.__gen_thin_file_in(self.arch_dir)

        self.__create_objs_dir_in(self.arch_dir)
        self.__unarchive_thinned_lib_in(self.objs_dir)

        self.__extract_xar_to_dir(self.objs_dir)

        root = self.__extract_xml_from(self.xar_path)
        self.__patch_element_tree(root)

        self.__forward_to_obfuscation(self.args)
        self.__archive_objs(self.objs_dir, 
                    include_list = [self.obf_obj_path],  
                    exclude_list = [self.expected_obj_name])
    
    def final_path(self):
        """ get the final output """
        pre_condition(os.path.exists(self.obf_achv_path),
            u"Cannot find expected object file {} after extracting".format(self.obf_achv_path))

        return self.obf_achv_path

def main(args=None):
    """Run the program, can override args for testing."""
    if args is None:
        args = sys.argv
    args = parse_args(args) 
    ENV.initState(args) 

    input_lib = args.input_static_lib
    if not os.path.isfile(input_lib):
        ENV.error(u"Input macho file doesn't exist: {}".format(
                args.input_macho_file))

    working_base = args.wdir
    if not os.path.isdir(working_base):
        os.makedirs(working_base)

    archs = MachoType.getArch(input_lib) 
    name = os.path.basename(input_lib)
    cache = {} 

    for arch in archs:
        transformer = LibTransformer(input_lib, arch, args, working_base)
        transformer.run() 
        cache[arch] = transformer.final_path()

    obf_achv_list = cache.values()
    final_path = os.path.join(working_base, ''.join([name, '_obfuscated.a']))

    lipoc_job = CommandTool.LipoCreate(obf_achv_list, final_path, working_base).run()
    if lipoc_job.returncode != 0:
        ENV.error(u"Create lipo failed {}".format(final_path))


if __name__ == "__main__":
    main()