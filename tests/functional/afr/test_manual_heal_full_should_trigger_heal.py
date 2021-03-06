#  Copyright (C) 2015-2020  Red Hat, Inc. <http://www.redhat.com>
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License along
#  with this program; if not, write to the Free Software Foundation, Inc.,
#  51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.


from glusto.core import Glusto as g
from glustolibs.gluster.gluster_base_class import (GlusterBaseClass, runs_on)
from glustolibs.gluster.exceptions import ExecutionError
from glustolibs.gluster.brick_libs import get_all_bricks
from glustolibs.gluster.brick_ops import add_brick
from glustolibs.gluster.volume_libs import get_volume_type_info
from glustolibs.gluster.heal_libs import (monitor_heal_completion,
                                          is_heal_complete,
                                          is_volume_in_split_brain)
from glustolibs.gluster.heal_ops import trigger_heal_full
from glustolibs.misc.misc_libs import upload_scripts
from glustolibs.gluster.lib_utils import (form_bricks_list,
                                          collect_bricks_arequal)
from glustolibs.io.utils import (validate_io_procs,
                                 wait_for_io_to_complete,
                                 collect_mounts_arequal)


@runs_on([['distributed'], ['glusterfs']])
class TestSelfHeal(GlusterBaseClass):
    @classmethod
    def setUpClass(cls):
        # Calling GlusterBaseClass setUpClass
        cls.get_super_method(cls, 'setUpClass')()

        # Upload io scripts for running IO on mounts
        cls.script_upload_path = ("/usr/share/glustolibs/io/scripts/"
                                  "file_dir_ops.py")
        ret = upload_scripts(cls.clients, cls.script_upload_path)
        if not ret:
            raise ExecutionError("Failed to upload IO scripts to clients %s"
                                 % cls.clients)
        g.log.info("Successfully uploaded IO scripts to clients %s",
                   cls.clients)

        # Define x1 distributed volume
        cls.volume['voltype'] = {
            'type': 'distributed',
            'dist_count': 1,
            'transport': 'tcp'}

    def setUp(self):

        # Calling GlusterBaseClass setUp
        self.get_super_method(self, 'setUp')()
        self.all_mounts_procs, self.io_validation_complete = [], False

        # Setup Volume and Mount Volume
        ret = self.setup_volume_and_mount_volume(self.mounts)
        if not ret:
            raise ExecutionError("Failed to Setup_Volume and Mount_Volume")
        g.log.info("Successful in Setup Volume and Mount Volume")

    def tearDown(self):

        # If test method failed before validating IO, tearDown waits for the
        # IO's to complete and checks for the IO exit status
        if not self.io_validation_complete:
            ret = wait_for_io_to_complete(self.all_mounts_procs, self.mounts)
            if not ret:
                raise ExecutionError("IO failed on some of the clients")
            g.log.info("IO is successful on all mounts")

        # Cleanup and umount volume
        ret = self.unmount_volume_and_cleanup_volume(self.mounts)
        if not ret:
            raise ExecutionError("Failed to umount the vol & cleanup Volume")
        g.log.info("Successful in umounting the volume and Cleanup")

        # Calling GlusterBaseClass teardown
        self.get_super_method(self, 'tearDown')()

    def test_manual_heal_full_should_trigger_heal(self):
        """
        - Create a single brick volume
        - Add some files and directories
        - Get arequal from mountpoint
        - Add-brick such that this brick makes the volume a replica vol 1x3
        - Start heal full
        - Make sure heal is completed
        - Get arequals from all bricks and compare with arequal from mountpoint
        """
        # pylint: disable=too-many-statements,too-many-locals
        # Start IO on mounts
        self.all_mounts_procs = []
        for mount_obj in self.mounts:
            cmd = ("/usr/bin/env python %s create_deep_dirs_with_files "
                   "--dir-length 1 --dir-depth 1 --max-num-of-dirs 1 "
                   "--num-of-files 10 %s" % (self.script_upload_path,
                                             mount_obj.mountpoint))
            proc = g.run_async(mount_obj.client_system, cmd,
                               user=mount_obj.user)
            self.all_mounts_procs.append(proc)
            g.log.info("IO on %s:%s is started successfully",
                       mount_obj.client_system, mount_obj.mountpoint)
        self.io_validation_complete = False

        # Validate IO
        ret = validate_io_procs(self.all_mounts_procs, self.mounts)
        self.assertTrue(ret, "IO failed on some of the clients")
        self.io_validation_complete = True
        g.log.info("IO is successful on all mounts")

        # Get arequal for mount before adding bricks
        ret, arequals = collect_mounts_arequal(self.mounts)
        self.assertTrue(ret, 'Failed to get arequal')
        g.log.info('Getting arequal after healing is successful')
        mount_point_total = arequals[0].splitlines()[-1].split(':')[-1]

        # Form brick list to add
        bricks_to_add = form_bricks_list(self.mnode, self.volname, 2,
                                         self.servers, self.all_servers_info)
        g.log.info('Brick list to add: %s', bricks_to_add)

        # Add bricks
        ret, _, _ = add_brick(self.mnode, self.volname, bricks_to_add,
                              replica_count=3)
        self.assertFalse(ret, "Failed to add bricks %s" % bricks_to_add)
        g.log.info("Adding bricks is successful on volume %s", self.volname)

        # Make sure the newly added bricks are available in the volume
        # get the bricks for the volume
        bricks_list = get_all_bricks(self.mnode, self.volname)
        for brick in bricks_to_add:
            self.assertIn(brick, bricks_list,
                          'Brick %s is not in brick list' % brick)
        g.log.info('New bricks are present in the volume')

        # Make sure volume change from distribute to replicate volume
        vol_info_dict = get_volume_type_info(self.mnode, self.volname)
        vol_type = vol_info_dict['volume_type_info']['typeStr']
        self.assertEqual('Replicate', vol_type,
                         'Volume type is not converted to Replicate '
                         'after adding bricks')
        g.log.info('Volume type is successfully converted to Replicate '
                   'after adding bricks')

        # Start healing
        ret = trigger_heal_full(self.mnode, self.volname)
        self.assertTrue(ret, 'Heal is not started')
        g.log.info('Healing is started')

        # Monitor heal completion
        ret = monitor_heal_completion(self.mnode, self.volname)
        self.assertTrue(ret, 'Heal has not yet completed')

        # Check if heal is completed
        ret = is_heal_complete(self.mnode, self.volname)
        self.assertTrue(ret, 'Heal is not complete')
        g.log.info('Heal is completed successfully')

        # Check for split-brain
        ret = is_volume_in_split_brain(self.mnode, self.volname)
        self.assertFalse(ret, 'Volume is in split-brain state')
        g.log.info('Volume is not in split-brain state')

        # Get arequal on bricks and compare with mount_point_total
        # It should be the same
        ret, arequals = collect_bricks_arequal(bricks_list)
        self.assertTrue(ret, 'Failed to get arequal on bricks')
        for arequal in arequals:
            brick_total = arequal.splitlines()[-1].split(':')[-1]
            self.assertEqual(mount_point_total, brick_total,
                             'Arequals for mountpoint and %s are not equal')
        g.log.info('All arequals are equal for replicated')
