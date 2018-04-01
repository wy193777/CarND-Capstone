#!/usr/bin/env python
import rospy
from std_msgs.msg import Int32
from geometry_msgs.msg import PoseStamped, Pose
from styx_msgs.msg import TrafficLightArray, TrafficLight
from styx_msgs.msg import Lane
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
from light_classification.tl_classifier import TLClassifier
import tf
import cv2
import yaml
import math
import os

STATE_COUNT_THRESHOLD = 3


class TLDetector(object):
    def __init__(self):
        rospy.init_node('tl_detector')

        self.pose = None
        self.waypoints = []
        self.camera_image = None
        self.lights = []

        config_string = rospy.get_param("/traffic_light_config")
        self.config = yaml.load(config_string)
        self.stop_line_positions = self.config['stop_line_positions']

        rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb)
        rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)

        '''
        /vehicle/traffic_lights provides you with the location of the traffic
        light in 3D map space and helps you acquire an accurate ground truth
        data source for the traffic light classifier by sending the current
        color state of all traffic lights in the simulator. When testing on
        the vehicle, the color state will not be available. You'll need to
        rely on the position of the light and the camera image to predict it.
        '''
        rospy.Subscriber(
            '/vehicle/traffic_lights', TrafficLightArray, self.traffic_cb)
        rospy.Subscriber('/image_color', Image, self.image_cb)

        self.upcoming_red_light_pub = rospy.Publisher(
            '/traffic_waypoint', Int32, queue_size=1)

        self.bridge = CvBridge()
        self.light_classifier = TLClassifier()
        self.listener = tf.TransformListener()

        self.state = TrafficLight.UNKNOWN
        self.last_state = TrafficLight.UNKNOWN
        self.last_wp = -1
        self.state_count = 0

        # if collect data from the sim, turn the collect_data to True
        self.collect_data = False
        self.stop_line_waypoints = None
        self.light_line_pair = None
        rospy.spin()

    def pose_cb(self, msg):
        self.pose = msg

    def waypoints_cb(self, waypoints):
        self.waypoints = waypoints.waypoints

    def traffic_cb(self, msg):
        self.lights = msg.lights

    def image_cb(self, msg):
        """
        Identifies red lights in the incoming camera image and publishes the
        index of the waypoint closest to the red light's stop line to
        /traffic_waypoint

        Args:
            msg (Image): image from car-mounted camera

        """
        self.has_image = True
        self.camera_image = msg
        light_wp, state = self.process_traffic_lights()

        '''
        Publish upcoming red lights at camera frequency.
        Each predicted state has to occur `STATE_COUNT_THRESHOLD` number
        of times till we start using it. Otherwise the previous stable state is
        used.
        '''
        if self.state != state:
            self.state_count = 0
            self.state = state
        elif self.state_count >= STATE_COUNT_THRESHOLD:
            self.last_state = self.state
            light_wp = light_wp if state == TrafficLight.RED else -1
            self.last_wp = light_wp
            self.upcoming_red_light_pub.publish(Int32(light_wp))
        else:
            self.upcoming_red_light_pub.publish(Int32(self.last_wp))
        self.state_count += 1

    def get_closest_waypoint(self, pos_x, pos_y):
        """Identifies the closest path waypoint to the given position
            https://en.wikipedia.org/wiki/Closest_pair_of_points_problem
        Args:
            pose (Pose): position to match a waypoint to

        Returns:
            int: index of the closest waypoint in self.waypoints

        """
        # Done
        if self.waypoints is None:
            return None
        min_distance = 9999999.
        closed_indx = 0

        for indx, waypoint in enumerate(self.waypoints):
            waypoint_pos_x = waypoint.pose.pose.position.x
            waypoint_pos_y = waypoint.pose.pose.position.y

            distance = math.sqrt(
                (waypoint_pos_x - pos_x)**2 + (waypoint_pos_y - pos_y)**2)

            if distance < min_distance:
                min_distance = distance
                closed_indx = indx
        return closed_indx

    def get_light_state(self, light):
        """Determines the current color of the traffic light

        Args:
            light (TrafficLight): light to classify

        Returns:
            int: ID of traffic light color
                 (specified in styx_msgs/TrafficLight)

        """
        if(not self.has_image):
            self.prev_light_loc = None
            return False

        cv_image = self.bridge.imgmsg_to_cv2(self.camera_image, "bgr8")

        # Get classification
        return self.light_classifier.get_classification(cv_image)

    def process_traffic_lights(self):
        """Finds closest visible traffic light, if one exists, and determines its
            location and color

        Returns:
            int: index of waypoint closes to the upcoming stop line for a
                 traffic light (-1 if none exists)
            int: ID of traffic light color
                 (specified in styx_msgs/TrafficLight)

        """
        light = None
        ahead_stop_line_wp = -1
        # List of positions that correspond to the line to stop in front of
        # for a given intersection

        if(self.pose):
            car_waypoint = self.get_closest_waypoint(
                self.pose.pose.position.x, self.pose.pose.position.y)
            if self.stop_line_waypoints is None:
                # get the stop line waypoint list, this calculate just perform once
                self.stop_line_waypoints = self.get_stopline_waypoints()

            if (self.light_line_pair is None) and (len(self.lights) != 0):
                # get the traffic light and stop line waypoint pair list, this
                # calculate just perform once
                self.light_line_pair = self.get_light_line_pair()
                rospy.loginfo(self.light_line_pair)

            # find index of waypoint for the stop line ahead
            for ind, pair in enumerate(self.light_line_pair):
                if car_waypoint < pair[1]:
                    ahead_stop_line_wp = pair[1]
                    ahead_light_wp = pair[0]

                    if self.collect_data:
                        if abs(car_waypoint - ahead_stop_line_wp) < 100:
                            # the car is near the stop line, save the front
                            # camera img
                            self.save_img(ind)
                    break


        # TODO: light state detection need to be complete
        if light:
            state = self.get_light_state(light)

        return ahead_stop_line_wp, TrafficLight.UNKNOWN

    def get_stopline_waypoints(self):
        stop_line_wp = []
        for stop_line in self.stop_line_positions:
            x = stop_line[0]
            y = stop_line[1]
            indx = self.get_closest_waypoint(x, y)
            stop_line_wp.append(indx)
        return stop_line_wp

    def save_img(self, ind):
        """
        :param ind: the index of ahead traffic light in light_line_pair
        :return:
        """
        # collect the yellow light
        # if self.lights[ind].state != 1:
        #     return
        save_path = os.path.join(os.getcwd(), 'carla' + str(self.lights[ind].state))
        if not os.path.exists(save_path):
            os.mkdir(save_path)

        import time
        file_name = str(time.time())+'_'+str(self.lights[ind].state)+'.png'
        file_name = os.path.join(save_path, file_name)
        rospy.loginfo(file_name)
        cv_image = self.bridge.imgmsg_to_cv2(self.camera_image, "bgr8")
        cv2.imwrite(file_name, cv_image)

    def get_light_line_pair(self):
        """
           get the traffic light and stop line waypoint pair
           @return pair_list: a list like: [[light_waypoint, stop_waypoint], [... , ...], ...]
        """
        # the number of stop line and traffic light should be the same
        assert len(self.lights) == len(self.stop_line_waypoints)
        pair_list = []
        for i in range(len(self.lights)):
            light_pos = self.lights[i].pose.pose.position
            light_wp = self.get_closest_waypoint(light_pos.x, light_pos.y)
            pair_list.append([light_wp, self.stop_line_waypoints[i]])
        return pair_list


if __name__ == '__main__':
    try:
        TLDetector()
    except rospy.ROSInterruptException:
        rospy.logerr('Could not start traffic node.')
