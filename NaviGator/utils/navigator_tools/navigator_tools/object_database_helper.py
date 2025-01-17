"""Use the DBHelper class to interface with the Database without having to deal with ROS things."""
import asyncio
import sys
import time

import mil_tools as nt
import numpy as np
from nav_msgs.msg import Odometry
from navigator_msgs.srv import ObjectDBQuery, ObjectDBQueryRequest

from .missing_perception_object import MissingPerceptionObject

__author__ = "Tess Bianchi"


class DBHelper:
    """DBHelper class."""

    def __init__(self, nh):
        """Initialize the DB helper class."""
        self.found = set()
        self.nh = nh
        self.position = None
        self.rot = None
        self.new_object_subscriber = None
        self.ensuring_objects = False
        self.ensuring_object_dep = None
        self.ensuring_object_cb = None
        self.looking_for = None
        self.is_found = False

    async def init_(self, navigator=None):
        """Initialize the axros parts of the DBHelper."""
        # self._sub_database = yield self.nh.subscribe('/database/objects', PerceptionObjectArray, self.object_cb)
        self._database = self.nh.get_service_client("/database/requests", ObjectDBQuery)
        self.navigator = navigator
        if navigator is None:
            self._odom_sub = self.nh.subscribe("/odom", Odometry, self._odom_cb)
            await self._odom_sub.setup()
        else:
            self.position = await navigator.tx_pose()
            self.position = self.position[0]
        return self

    def _odom_cb(self, odom):
        self.position, self.rot = nt.odometry_to_numpy(odom)[0]

    async def get_object_by_id(self, my_id):
        print(my_id)
        req = ObjectDBQueryRequest()
        req.name = "all"
        resp = await self._database(req)
        ans = [obj for obj in resp.objects if obj.id == my_id][0]
        return ans

    async def begin_observing(self, cb):
        """
        Get notified when a new object is added to the database.

        cb : The callback that is called when a new object is added to the database
        """
        self.new_object_subscriber = cb
        req = ObjectDBQueryRequest()
        req.name = "all"
        resp = await self._database(req)
        req.name = "All"
        resp1 = await self._database(req)
        for o in resp.objects:
            # The is upper call is because the first case is upper case if it is a 'fake' object... WHYYYYY
            if o.name not in self.found:
                self.found.add(o.name)
                self.new_object_subscriber(o)

        for o in resp1.objects:
            # The is upper call is because the first case is upper case if it is a 'fake' object... WHYYYYY
            if o.name not in self.found:
                self.found.add(o.name)
                self.new_object_subscriber(o)

    async def get_unknown_and_low_conf(self):
        req = ObjectDBQueryRequest()
        req.name = "all"
        resp = await self._database(req)
        m = []
        for o in resp.objects:
            if o.name == "unknown":
                m.append(o)
            elif o.confidence < 50:
                pass
                # m.append(o)
        return m

    def set_looking_for(self, name):
        self.looking_for = name

    def is_found_func(self):
        if self.is_found:
            self.looking_for = None
            self.is_found = False
            return True
        return False

    def object_cb(self, perception_objects):
        """Callback for the object database."""
        self.total_num = len(perception_objects.objects)
        for o in perception_objects.objects:
            if o.name == self.looking_for:
                self.is_found = True
            if o.name not in self.found:
                self.found.add(o.name)
                if self.new_object_subscriber is not None:
                    self.new_object_subscriber(o)
        if self.ensuring_objects:
            missings_objs = []
            names = (o.name for o in perception_objects.objects)
            for o in self.ensuring_object_dep:
                if o not in names:
                    missings_objs.append(o)
            if len(missings_objs) > 0:
                self.ensuring_object_cb(missings_objs)

    def remove_found(self, name):
        """Remove an object that has been listed as found."""
        self.found.remove(name)

    def ensure_object_permanence(self, object_dep, cb):
        """Ensure that all the objects in the object_dep list remain in the database.
        Call the callback if this isn't true."""
        if object_dep is None or cb is None:
            return
        self.ensuring_objects = True
        self.ensuring_object_cb = cb
        self.ensuring_object_dep = object_dep

    def stop_ensuring_object_permanence(self):
        """Stop ensuring that objects remain in the database."""
        self.ensuring_objects = False

    def _wait_for_position(self, timeout=10):
        count = 0
        while self.position is None:
            if self.navigator is not None:
                self.position = self.navigator.pose[0]
            if count > timeout:
                return False
            count += 1
            time.sleep(1)
        return True

    async def get_closest_object(self, objects):
        """Get the closest mission."""
        pobjs = []
        for obj in objects:
            req = ObjectDBQueryRequest()
            req.name = obj.name
            resp = await self._database(req)
            if len(resp.objects) != 0:
                pobjs.extend(resp.objects)

        if len(pobjs) == 0:
            raise MissingPerceptionObject("All")

        min_dist = sys.maxsize
        min_obj = None
        for o in pobjs:
            dist = await self._dist(o)
            if dist < min_dist:
                min_dist = dist
                min_obj = o

        return min_obj

    async def _dist(self, x):
        if self.position is None:
            success = asyncio.create_task(self._wait_for_position)
            if not success:
                raise Exception("There is a problem with odom.")
        if self.navigator is not None:
            position = await self.navigator.tx_pose()
            position = position[0]
            self.position = position
        return np.linalg.norm(nt.rosmsg_to_numpy(x.position) - self.position)

    async def get_object(
        self,
        object_name,
        volume_only=False,
        thresh=50,
        thresh_strict=50,
    ):
        """Get an object from the database."""
        if volume_only:
            req = ObjectDBQueryRequest()
            req.name = object_name
            resp = await self._database(req)
            if not resp.found:
                raise MissingPerceptionObject(object_name)
            return resp.objects
        else:
            req = ObjectDBQueryRequest()
            req.name = "all"
            resp = await self._database(req)
            closest_potential_object = None
            min_dist = sys.maxsize
            actual_objects = []
            for o in resp.objects:
                distance = await self._dist(o)
                if o.name == object_name and distance < thresh_strict:
                    actual_objects.append(o)
                if distance < thresh and distance < min_dist:
                    min_dist = distance
                    closest_potential_object = o
            # print [x.name for x in actual_objects]
            # print closest_potential_object.name
            # sys.exit()

            if len(actual_objects) == 0 and min_dist == sys.maxsize:
                raise MissingPerceptionObject(object_name)

            if len(actual_objects) > 1:
                min_dist = sys.maxsize
                min_obj = None
                for o in actual_objects:
                    dist = await self._dist(o)
                    if dist < min_dist:
                        min_dist = dist
                        min_obj = o
                return min_obj

            if len(actual_objects) == 1:
                return actual_objects[0]

            return closest_potential_object

    def wait_for_additional_objects(self, timeout=60):
        num_items = self.num_items
        start = time.time()
        while timeout < time.time() - start:
            if self.num_items > num_items:
                return True
        return False

    def set_color(self, color, name):
        """Set the color of an object in the database."""
        raise NotImplementedError

    def set_fake_position(self, pos):
        """Set the position of a fake perception object."""
        raise NotImplementedError

    async def get_objects_in_radius(self, pos, radius, objects="all"):
        req = ObjectDBQueryRequest()
        req.name = "all"
        resp = await self._database(req)
        ans = []

        if not resp.found:
            return ans

        for o in resp.objects:
            if objects == "all" or o.name in objects:
                dist = np.linalg.norm(pos - nt.rosmsg_to_numpy(o.position))
                if dist < radius:
                    ans.append(o)
        return ans
